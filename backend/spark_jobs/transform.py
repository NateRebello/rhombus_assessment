"""PySpark transform: read CSV/Excel, apply regex replace or normalisation UDF, write Parquet.

No Django/Celery imports — callable standalone via `python -m spark_jobs.transform`.
"""
import logging
import os
from pathlib import Path
from typing import Callable, Optional

from pyspark.sql import functions as F
from pyspark.sql.types import StringType
from pyspark.sql.utils import AnalysisException

from spark_jobs.session import get_session

logger = logging.getLogger(__name__)

_ALLOWED_NORMALIZE = frozenset({"none", "e164", "iso8601"})
_TARGET_PARTITION_BYTES = 128 * 1024 * 1024


def _normalize_e164_fn(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    import re
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    if 7 <= len(digits) <= 15:
        return f"+{digits}"
    return value


def _normalize_iso8601_fn(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    from datetime import datetime
    formats = [
        "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%d-%m-%Y",
        "%Y/%m/%d", "%Y-%m-%d",
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
        "%m/%d/%y", "%d/%m/%y", "%Y%m%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(str(value).strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
    return value


def _infer_partition_count(file_path: str) -> int:
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return 4

    count = max(1, size // _TARGET_PARTITION_BYTES)
    return min(count, 64)


def run(
    file_path: str,
    target_column: str,
    regex: str,
    replacement: str,
    result_base_dir: str,
    progress_callback: Optional[Callable[[float], None]] = None,
    normalize: str = "none",
) -> dict:
    if normalize not in _ALLOWED_NORMALIZE:
        raise ValueError(
            f"Invalid normalize mode {normalize!r}. Must be one of {_ALLOWED_NORMALIZE}."
        )
    if not Path(file_path).exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    def _progress(pct: float) -> None:
        if progress_callback:
            try:
                progress_callback(pct)
            except Exception:
                pass

    spark = get_session()
    _progress(0.05)

    ext = Path(file_path).suffix.lower()
    try:
        if ext == ".csv":
            df = (
                spark.read
                .option("header", "true")
                .option("inferSchema", "true")
                .option("multiLine", "true")
                .option("escape", '"')
                .csv(file_path)
            )
        elif ext in (".xlsx", ".xls"):
            import pandas as pd

            if ext == ".xlsx":
                pdf = pd.read_excel(file_path, engine="openpyxl")
            else:
                pdf = pd.read_excel(file_path, engine="xlrd")
            df = spark.createDataFrame(pdf)
        else:
            raise ValueError(f"Unsupported file extension: {ext}")
    except AnalysisException as exc:
        raise ValueError(f"Spark could not read input file: {exc}") from exc

    _progress(0.15)

    if target_column not in df.columns:
        raise ValueError(
            f"Column '{target_column}' not found. Available columns: {df.columns}"
        )

    num_partitions = _infer_partition_count(file_path)
    logger.info(
        "Repartitioning to %d partitions (file size: %d bytes)",
        num_partitions,
        os.path.getsize(file_path),
    )
    df = df.repartition(num_partitions)
    _progress(0.30)

    col_str = F.col(target_column).cast("string")

    if normalize == "none":
        df = df.withColumn(
            target_column,
            F.regexp_replace(col_str, regex, replacement),
        )
    else:
        if normalize == "e164":
            normalize_udf = F.udf(_normalize_e164_fn, StringType())
        else:
            normalize_udf = F.udf(_normalize_iso8601_fn, StringType())

        df = df.withColumn(
            target_column,
            F.when(col_str.rlike(regex), normalize_udf(col_str)).otherwise(col_str),
        )
        logger.info("Applied %s normalisation UDF to column '%s'", normalize, target_column)

    _progress(0.70)

    result_path = str(Path(result_base_dir) / "output.parquet")
    (
        df.write
        .mode("overwrite")
        .option("compression", "snappy")
        .parquet(result_path)
    )
    _progress(0.95)

    row_count = spark.read.parquet(result_path).count()
    _progress(1.0)

    logger.info("Transform complete: %d rows → %s", row_count, result_path)
    return {
        "result_path": result_path,
        "row_count": row_count,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run PySpark transform standalone")
    parser.add_argument("--file", required=True)
    parser.add_argument("--column", required=True)
    parser.add_argument("--regex", required=True)
    parser.add_argument("--replacement", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--normalize", default="none", choices=["none", "e164", "iso8601"])
    args = parser.parse_args()

    result = run(
        file_path=args.file,
        target_column=args.column,
        regex=args.regex,
        replacement=args.replacement,
        result_base_dir=args.out,
        progress_callback=lambda p: print(f"Progress: {p:.0%}"),
        normalize=args.normalize,
    )
    print(result)

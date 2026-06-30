"""
PySpark transform: read → repartition → apply transform → write Parquet.

Module contract (what callers can rely on):
  - Entrypoint is run().  Call it with file_path, column name, regex, and an
    output directory; get back a result dict.
  - This module has NO knowledge of Django, Celery, Redis, or HTTP.  It is a
    pure data-processing module that could be invoked from the command line,
    a Jupyter notebook, or any other Python process.

Transform modes (controlled by `normalize` parameter):
  "none"    — standard regexp_replace with a literal replacement string.
  "e164"    — match rows whose target column matches regex, then normalise the
              entire cell value to E.164 phone format (+<country><digits>).
  "iso8601" — match rows whose target column matches regex, then normalise the
              entire cell value to ISO 8601 date format (YYYY-MM-DD).

The normalize value is validated by the caller (pipeline.py reads it from
tasks.llm_regex.ALLOWED_NORMALIZE) before being passed here, so this module
treats it as already-safe input and only performs a secondary guard.
"""
import logging
import os
import re as _re
from pathlib import Path
from typing import Callable, Optional

from pyspark.sql import functions as F
from pyspark.sql.types import StringType
from pyspark.sql.utils import AnalysisException

from spark_jobs.session import get_session

logger = logging.getLogger(__name__)


# ── Normalisation UDFs ────────────────────────────────────────────────────────
# These functions run inside PySpark executors (serialised via cloudpickle).
# They must be self-contained: no module-level imports, no closures over
# non-picklable objects.

def _normalize_e164_fn(value: Optional[str]) -> Optional[str]:
    """Convert a phone-number string to E.164 format (+<country><digits>)."""
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
    return value  # unparseable — leave unchanged


def _normalize_iso8601_fn(value: Optional[str]) -> Optional[str]:
    """Convert common date formats to ISO 8601 (YYYY-MM-DD)."""
    if value is None:
        return value
    from datetime import datetime
    FORMATS = [
        "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%d-%m-%Y",
        "%Y/%m/%d", "%Y-%m-%d",
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
        "%m/%d/%y", "%d/%m/%y", "%Y%m%d",
    ]
    for fmt in FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
    return value  # unparseable — leave unchanged


# Secondary allowlist guard (primary validation is in tasks/llm_regex.py).
_ALLOWED_NORMALIZE = frozenset({"none", "e164", "iso8601"})

# Target partition size when repartitioning the input DataFrame.
# 128 MB matches HDFS default block size and is a reasonable default for
# Parquet output files — each partition becomes one Parquet file.
_TARGET_PARTITION_BYTES = 128 * 1024 * 1024  # 128 MB


def _infer_partition_count(file_path: str) -> int:
    """
    Heuristically choose a partition count based on input file size.

    Using too few partitions underutilises parallelism; too many creates
    excessive small files and scheduler overhead.  ~128 MB per partition is
    the standard heuristic from the Spark documentation.
    """
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return 4  # safe fallback

    count = max(1, size // _TARGET_PARTITION_BYTES)
    # Cap at 64 for local mode — beyond this Spark's task scheduling overhead
    # exceeds the benefit on a single machine.
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
    """
    Apply a regex-based transform to target_column and write Parquet output.

    Args:
        file_path:        Absolute path to the uploaded CSV or Excel file.
        target_column:    Column name to apply the transform on.
        regex:            Validated regex string (Java-compatible).
        replacement:      Replacement string for normalize="none" mode.
        result_base_dir:  Directory where Parquet partitions will be written.
        progress_callback: Optional callable(float 0..1) for progress updates.
        normalize:        "none" | "e164" | "iso8601".  When not "none", the
                          matched cells are normalised via a Spark UDF instead
                          of replaced with a literal string.

    Returns:
        dict:
            result_path (str)  – absolute path to the Parquet output directory.
            row_count   (int)  – total rows in the output.

    Raises:
        FileNotFoundError   – file_path does not exist (terminal).
        ValueError          – target_column not found / bad normalize (terminal).
        AnalysisException   – Spark cannot parse the file (terminal).
    """
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
                pass  # progress updates are best-effort

    spark = get_session()
    _progress(0.05)

    # ── Read input file ───────────────────────────────────────────────────────
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
            # PySpark does not natively read Excel; convert via pandas first.
            # This is acceptable for files that fit in driver memory (~500 MB).
            # For larger Excel files the upstream upload validation should reject
            # them or convert to CSV before reaching this point.
            import pandas as pd

            if ext == ".xlsx":
                pdf = pd.read_excel(file_path, engine="openpyxl")
            else:
                pdf = pd.read_excel(file_path, engine="xlrd")
            df = spark.createDataFrame(pdf)
        else:
            raise ValueError(f"Unsupported file extension: {ext}")
    except AnalysisException as exc:
        # Spark could not parse the file (e.g. corrupt CSV, schema mismatch).
        # Re-raise as ValueError so pipeline.py treats it as a terminal error
        # and does not retry.
        raise ValueError(f"Spark could not read input file: {exc}") from exc

    _progress(0.15)

    # ── Validate target column ────────────────────────────────────────────────
    if target_column not in df.columns:
        raise ValueError(
            f"Column '{target_column}' not found. Available columns: {df.columns}"
        )

    # ── Repartition explicitly ────────────────────────────────────────────────
    # Repartitioning before the transform ensures work is spread evenly across
    # available cores and that each output Parquet file is ~128 MB (avoiding
    # the "many small files" problem that hurts downstream readers).
    num_partitions = _infer_partition_count(file_path)
    logger.info(
        "Repartitioning to %d partitions (file size: %d bytes)",
        num_partitions,
        os.path.getsize(file_path),
    )
    df = df.repartition(num_partitions)
    _progress(0.30)

    # ── Apply transform ───────────────────────────────────────────────────────
    col_str = F.col(target_column).cast("string")

    if normalize == "none":
        # Standard regexp_replace: every match is replaced with a literal string.
        df = df.withColumn(
            target_column,
            F.regexp_replace(col_str, regex, replacement),
        )
    else:
        # Normalisation mode: matched cells are passed through a UDF.
        # Unmatched cells are left unchanged.
        if normalize == "e164":
            normalize_udf = F.udf(_normalize_e164_fn, StringType())
        else:  # iso8601
            normalize_udf = F.udf(_normalize_iso8601_fn, StringType())

        df = df.withColumn(
            target_column,
            F.when(col_str.rlike(regex), normalize_udf(col_str)).otherwise(col_str),
        )
        logger.info("Applied %s normalisation UDF to column '%s'", normalize, target_column)

    _progress(0.70)

    # ── Write partitioned Parquet output ──────────────────────────────────────
    result_path = str(Path(result_base_dir) / "output.parquet")
    (
        df.write
        .mode("overwrite")
        .option("compression", "snappy")
        .parquet(result_path)
    )
    _progress(0.95)

    # ── Count rows (forces a separate Spark action) ───────────────────────────
    # We re-read the output to get an authoritative row count rather than
    # using df.count() before writing (which would execute the plan twice).
    row_count = spark.read.parquet(result_path).count()
    _progress(1.0)

    logger.info("Transform complete: %d rows → %s", row_count, result_path)
    return {
        "result_path": result_path,
        "row_count": row_count,
    }


if __name__ == "__main__":
    # Standalone smoke test:
    # python -m spark_jobs.transform \
    #   --file /app/uploads/test.csv \
    #   --column email \
    #   --regex '[\w.]+@' \
    #   --replacement '[REDACTED]@' \
    #   --out /tmp/result
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

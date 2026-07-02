"""Extract column header samples from uploaded CSV/Excel for PII suggestion."""
from pathlib import Path

import pandas as pd

ALLOWED_UPLOAD_EXTENSIONS = frozenset({".csv", ".xlsx", ".xls"})


def extract_column_samples(
    uploaded_file,
    *,
    max_rows: int = 30,
    max_samples: int = 8,
) -> dict[str, list[str]]:
    ext = Path(uploaded_file.name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext or '(none)'}'. Allowed: .csv, .xlsx, .xls"
        )

    uploaded_file.seek(0)
    if ext == ".csv":
        df = pd.read_csv(uploaded_file, nrows=max_rows)
    elif ext == ".xlsx":
        df = pd.read_excel(uploaded_file, nrows=max_rows, engine="openpyxl")
    else:
        df = pd.read_excel(uploaded_file, nrows=max_rows, engine="xlrd")

    samples: dict[str, list[str]] = {}
    for col in df.columns:
        col_name = str(col)[:256]
        vals: list[str] = []
        for raw in df[col].dropna().head(max_samples):
            text = str(raw).strip()
            if text:
                vals.append(text)
        if vals:
            samples[col_name] = vals

    return samples

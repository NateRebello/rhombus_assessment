"""Convert selected CSV fixtures in this directory to .xlsx for Excel upload testing."""
from pathlib import Path

import pandas as pd

NAMES = ("smoke_test", "demo_emails", "demo_phones", "demo_pii")


def main() -> None:
    root = Path(__file__).resolve().parent
    for name in NAMES:
        csv_path = root / f"{name}.csv"
        if not csv_path.exists():
            print(f"skip (missing): {csv_path.name}")
            continue
        xlsx_path = root / f"{name}.xlsx"
        pd.read_csv(csv_path).to_excel(xlsx_path, index=False, engine="openpyxl")
        print(f"wrote {xlsx_path.name}")


if __name__ == "__main__":
    main()

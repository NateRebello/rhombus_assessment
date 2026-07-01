"""
Generate a large CSV file for load-testing the Rhombus platform.

Usage:
    python generate_large_csv.py --rows 5000000 --out sample.csv

The generated file contains realistic-looking columns that exercise the
regex engine: email addresses, phone numbers, free-text names, and random
numeric IDs.  This lets you test patterns like "find all Gmail addresses" or
"redact US phone numbers" against a multi-million-row dataset without needing
real data.
"""
import argparse
import csv
import random
import string
import sys
from pathlib import Path

FIRST_NAMES = [
    "Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Heidi",
    "Ivan", "Judy", "Karl", "Laura", "Mallory", "Niaj", "Olivia", "Peggy",
    "Quinn", "Romeo", "Sybil", "Trent", "Uma", "Victor", "Wendy", "Xavier",
    "Yvonne", "Zach",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
]

EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "company.io", "example.org", "test.net", "acme.co",
]

US_AREA_CODES = [
    "212", "310", "415", "312", "713", "206", "617", "305",
    "404", "602", "503", "702", "503", "816", "901",
]

CITIES = [
    "New York", "Los Angeles", "Chicago", "Houston", "Phoenix",
    "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Jose",
]

NOTE_TEMPLATES = [
    "Customer contacted on {date} regarding order #{order}.",
    "Follow up needed for account #{order}.",
    "Billing issue resolved — ref #{order}.",
    "Left voicemail at {phone}.",
    "Email sent to {email}.",
    "No issues reported.",
    "Escalated to tier 2 support.",
    "Closed without action.",
]


def _random_email() -> str:
    user = random.choice(FIRST_NAMES).lower() + "." + random.choice(LAST_NAMES).lower()
    suffix = "".join(random.choices(string.digits, k=random.randint(0, 3)))
    domain = random.choice(EMAIL_DOMAINS)
    return f"{user}{suffix}@{domain}"


def _random_phone() -> str:
    area = random.choice(US_AREA_CODES)
    exchange = random.randint(200, 999)
    number = random.randint(1000, 9999)
    fmt = random.choice(["({a}) {e}-{n}", "{a}-{e}-{n}", "{a}.{e}.{n}"])
    return fmt.format(a=area, e=exchange, n=number)


def _random_note(email: str, phone: str, row_id: int) -> str:
    template = random.choice(NOTE_TEMPLATES)
    date = f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
    return template.format(email=email, phone=phone, order=row_id, date=date)


COLUMNS = [
    "id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "city",
    "account_balance",
    "notes",
]


def generate(row_count: int, out_path: Path, chunk_size: int = 50_000) -> None:
    print(f"Generating {row_count:,} rows -> {out_path}", flush=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)

        written = 0
        while written < row_count:
            batch = min(chunk_size, row_count - written)
            rows = []
            for i in range(batch):
                row_id = written + i + 1
                first = random.choice(FIRST_NAMES)
                last = random.choice(LAST_NAMES)
                email = _random_email()
                phone = _random_phone()
                rows.append([
                    row_id,
                    first,
                    last,
                    email,
                    phone,
                    random.choice(CITIES),
                    round(random.uniform(0, 100_000), 2),
                    _random_note(email, phone, row_id),
                ])
            writer.writerows(rows)
            written += batch
            pct = written / row_count * 100
            print(f"\r  {written:>10,} / {row_count:,} rows ({pct:.1f}%)  ", end="", flush=True)

    print(f"\nDone. File size: {out_path.stat().st_size / 1024 / 1024:.1f} MB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a large test CSV")
    parser.add_argument(
        "--rows",
        type=int,
        default=1_000_000,
        help="Number of rows to generate (default: 1_000_000)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="sample.csv",
        help="Output file path (default: sample.csv)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()

    random.seed(args.seed)
    generate(args.rows, Path(args.out))


if __name__ == "__main__":
    main()

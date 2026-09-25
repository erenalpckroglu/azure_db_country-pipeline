"""
Exports everything needed to keep working offline once the Azure
subscription is gone.

From Azure SQL Database:
    price_clean, excluded_records -> data/curated/*.parquet and *.csv
From Blob Storage:
    fxrates/*.csv                 -> data/fx/
    latest filtered snapshot      -> data/filtered/
    latest raw file (optional)    -> data/raw/  (large, git-ignored)

Required environment variables:
    SQL_SERVER, SQL_DATABASE, SQL_USERNAME, SQL_PASSWORD
    STORAGE_CONNECTION_STRING   (only for the Blob part)

Usage (from the repository root):
    python scripts/export_to_local.py
    python scripts/export_to_local.py --include-raw
"""
import argparse
import datetime as dt
import decimal
import os
import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")
SQL_TABLES = ["price_clean", "excluded_records"]


def normalize_types(df: pd.DataFrame) -> pd.DataFrame:
    """SQL DECIMAL arrives as Python Decimal and DATE as datetime.date.
    Convert them so Parquet stores clean float and date types."""
    for col in df.columns:
        sample = df[col].dropna()
        if sample.empty:
            continue
        first = sample.iloc[0]
        if isinstance(first, decimal.Decimal):
            df[col] = df[col].astype(float)
        elif isinstance(first, dt.date) and not isinstance(first, dt.datetime):
            df[col] = pd.to_datetime(df[col])
    return df


def connect_with_retry(max_attempts: int = 5, delay_seconds: int = 12):
    """Retries on Azure's transient error 40613 while a serverless DB resumes."""
    import pymssql

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return pymssql.connect(
                server=os.environ["SQL_SERVER"],
                user=os.environ["SQL_USERNAME"],
                password=os.environ["SQL_PASSWORD"],
                database=os.environ["SQL_DATABASE"],
            )
        except Exception as e:
            last_error = e
            print(f"  SQL connect attempt {attempt}/{max_attempts} failed, retrying in {delay_seconds}s...")
            time.sleep(delay_seconds)
    raise last_error


def export_sql() -> None:
    out = DATA_DIR / "curated"
    out.mkdir(parents=True, exist_ok=True)
    conn = connect_with_retry()
    try:
        for table in SQL_TABLES:
            df = normalize_types(pd.read_sql(f"SELECT * FROM {table}", conn))
            df.to_parquet(out / f"{table}.parquet", index=False)
            df.to_csv(out / f"{table}.csv", index=False)
            print(f"  {table}: {len(df):,} rows")
    finally:
        conn.close()


def download_blob(container, blob_name: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as f:
        f.write(container.download_blob(blob_name).readall())
    print(f"  {container.container_name}/{blob_name} -> {target}")


def latest_blob(container, prefix: str):
    blobs = list(container.list_blobs(name_starts_with=prefix))
    return max(blobs, key=lambda b: b.last_modified) if blobs else None


def export_blobs(include_raw: bool) -> None:
    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient.from_connection_string(os.environ["STORAGE_CONNECTION_STRING"])

    fx = service.get_container_client("fxrates")
    for blob in fx.list_blobs():
        download_blob(fx, blob.name, DATA_DIR / "fx" / blob.name)

    filtered = service.get_container_client("filtered")
    newest = latest_blob(filtered, "gm_gb_filtered_")
    if newest:
        download_blob(filtered, newest.name, DATA_DIR / "filtered" / newest.name)

    if include_raw:
        raw = service.get_container_client("raw")
        newest = latest_blob(raw, "global_")
        if newest:
            download_blob(raw, newest.name, DATA_DIR / "raw" / newest.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-raw", action="store_true", help="also download the large raw source file")
    args = parser.parse_args()

    print("Exporting SQL tables...")
    export_sql()

    if os.environ.get("STORAGE_CONNECTION_STRING"):
        print("Downloading Blob Storage files...")
        export_blobs(args.include_raw)
    else:
        print("STORAGE_CONNECTION_STRING not set - skipping Blob Storage files.")

    print("Done.")


if __name__ == "__main__":
    main()

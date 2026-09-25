import azure.functions as func
import logging
import pandas as pd
import requests
import pymssql
from azure.storage.blob import BlobServiceClient
import os
from datetime import datetime, timezone
import io
import re
import time

app = func.FunctionApp()

UNIT_MAP = {
    "KG": ("KG", 1.0),
    "125 G": ("KG", 0.125),
    "70 G": ("KG", 0.07),
    "200 G": ("KG", 0.2),
    "L": ("L", 1.0),
    "Unit": ("Unit", 1.0),
}

BUSINESS_KEY_COLS = ["adm0_name", "adm1_name", "mkt_name", "cm_name", "pt_name", "um_name", "mp_month", "mp_year"]


def connect_with_retry(autocommit=True, max_attempts=5, delay_seconds=12):
    """
    Connects to the serverless SQL Database, retrying on Azure's transient
    error 40613 ("database is not currently available"), which occurs while
    the database is mid-resume from auto-pause.
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return pymssql.connect(
                server=os.environ["SQL_SERVER"],
                user=os.environ["SQL_USERNAME"],
                password=os.environ["SQL_PASSWORD"],
                database=os.environ["SQL_DATABASE"],
                autocommit=autocommit,
            )
        except Exception as e:
            last_error = e
            logging.info(f"SQL connect attempt {attempt}/{max_attempts} failed ({type(e).__name__}), retrying in {delay_seconds}s...")
            time.sleep(delay_seconds)
    raise last_error


def wake_up_sql() -> None:
    """
    Fires a trivial query early so the serverless SQL Database has time to
    resume from auto-pause while the rest of the pipeline (download, filter,
    clean) is still running.
    """
    try:
        conn = connect_with_retry(max_attempts=2, delay_seconds=5)
        conn.cursor().execute("SELECT 1")
        conn.close()
        logging.info("wake_up_sql: SQL Database is awake.")
    except Exception:
        logging.info("wake_up_sql: initial ping failed, continuing anyway - write_to_sql will retry on connect.")


def update_fx_current(blob_service: BlobServiceClient) -> None:
    fx_container = blob_service.get_container_client("fxrates")
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    try:
        existing_bytes = fx_container.download_blob("fx_rates_current.csv").readall()
        current_df = pd.read_csv(io.BytesIO(existing_bytes))
    except Exception:
        current_df = pd.DataFrame(columns=["year", "month", "currency", "rate_lcu_per_usd", "fetched_date", "source"])

    already_logged = ((current_df["year"] == year) & (current_df["month"] == month)).any()
    if already_logged:
        logging.info(f"FX rate for {year}-{month:02d} already logged, skipping fetch.")
        return

    response = requests.get(
        "https://api.frankfurter.dev/v2/rates",
        params={"base": "USD", "quotes": "GMD,XOF"},
        timeout=10,
    )
    response.raise_for_status()
    records = response.json()

    new_rows = pd.DataFrame([
        {
            "year": year,
            "month": month,
            "currency": rec["quote"],
            "rate_lcu_per_usd": rec["rate"],
            "fetched_date": now.strftime("%Y-%m-%d"),
            "source": "frankfurter_latest",
        }
        for rec in records
    ])
    current_df = pd.concat([current_df, new_rows], ignore_index=True)

    buf = io.StringIO()
    current_df.to_csv(buf, index=False)
    fx_container.upload_blob(name="fx_rates_current.csv", data=buf.getvalue(), overwrite=True)
    logging.info(f"Logged FX rates for {year}-{month:02d}.")


def load_fx_lookup(blob_service: BlobServiceClient) -> dict:
    fx_container = blob_service.get_container_client("fxrates")
    lookup = {}

    hist_bytes = fx_container.download_blob("fx_rates_historical.csv").readall()
    hist_df = pd.read_csv(io.BytesIO(hist_bytes))
    for _, row in hist_df.iterrows():
        key = (int(row["year"]), int(row["month"]), row["currency"])
        lookup[key] = (float(row["rate_lcu_per_usd"]), f"{int(row['year'])}-{int(row['month']):02d}-01")

    try:
        current_bytes = fx_container.download_blob("fx_rates_current.csv").readall()
        current_df = pd.read_csv(io.BytesIO(current_bytes))
        for _, row in current_df.iterrows():
            key = (int(row["year"]), int(row["month"]), row["currency"])
            lookup[key] = (float(row["rate_lcu_per_usd"]), str(row["fetched_date"]))
    except Exception:
        pass

    return lookup


def clean_and_standardize(df: pd.DataFrame, fx_lookup: dict, source_file: str):
    excluded_rows = []

    dedup_key = BUSINESS_KEY_COLS + ["mp_price", "cur_name"]
    is_dup = df.duplicated(subset=dedup_key, keep="first")
    for _, row in df[is_dup].iterrows():
        rec = row.to_dict()
        rec["reason"] = "duplicate on business key + price + currency - one copy kept"
        rec["source_file"] = source_file
        excluded_rows.append(rec)
    df = df[~is_dup].copy()

    key_groups = df.groupby(BUSINESS_KEY_COLS)[["mp_price", "cur_name"]].nunique()
    conflicting_keys = set(
        key_groups[(key_groups["mp_price"] > 1) | (key_groups["cur_name"] > 1)].index
    )

    def is_conflicting(row):
        return tuple(row[c] for c in BUSINESS_KEY_COLS) in conflicting_keys

    conflict_mask = df.apply(is_conflicting, axis=1)
    for _, row in df[conflict_mask].iterrows():
        rec = row.to_dict()
        rec["reason"] = "duplicate business key with conflicting price or currency - needs manual review"
        rec["source_file"] = source_file
        excluded_rows.append(rec)
    df = df[~conflict_mask].copy()

    clean_rows = []
    for _, row in df.iterrows():
        um = row["um_name"]
        if um not in UNIT_MAP:
            rec = row.to_dict()
            rec["reason"] = f"unknown unit '{um}' - no conversion mapping"
            rec["source_file"] = source_file
            excluded_rows.append(rec)
            continue

        unit_category, kg_factor = UNIT_MAP[um]
        price_per_std_unit = row["mp_price"] / kg_factor

        fx_key = (int(row["mp_year"]), int(row["mp_month"]), row["cur_name"])
        fx = fx_lookup.get(fx_key)
        if fx is None:
            rec = row.to_dict()
            rec["reason"] = f"no FX rate available for {row['cur_name']} {row['mp_year']}-{int(row['mp_month']):02d}"
            rec["source_file"] = source_file
            excluded_rows.append(rec)
            continue

        fx_rate, fx_date = fx
        price_usd = price_per_std_unit / fx_rate

        clean_rows.append({
            "adm0_name": row["adm0_name"],
            "adm1_name": row.get("adm1_name"),
            "mkt_name": row["mkt_name"],
            "cm_name": row["cm_name"],
            "cur_name": row["cur_name"],
            "pt_name": row["pt_name"],
            "mp_month": int(row["mp_month"]),
            "mp_year": int(row["mp_year"]),
            "mp_price": float(row["mp_price"]),
            "um_name": um,
            "unit_category_calc": unit_category,
            "price_per_std_unit_calc": price_per_std_unit,
            "fx_rate_calc": fx_rate,
            "fx_rate_date_calc": fx_date,
            "price_usd_calc": price_usd,
            "source_file": source_file,
        })

    clean_df = pd.DataFrame(clean_rows)
    excluded_df = pd.DataFrame(excluded_rows)
    return clean_df, excluded_df


def write_to_sql(clean_df: pd.DataFrame, excluded_df: pd.DataFrame) -> None:
    conn = connect_with_retry(autocommit=False, max_attempts=5, delay_seconds=12)
    cursor = conn.cursor()

    try:
        cursor.execute("TRUNCATE TABLE price_clean")
        cursor.execute("TRUNCATE TABLE excluded_records")

        if not clean_df.empty:
            clean_params = [
                (
                    row["adm0_name"], row["adm1_name"], row["mkt_name"], row["cm_name"],
                    row["cur_name"], row["pt_name"], row["mp_month"], row["mp_year"],
                    row["mp_price"], row["um_name"], row["unit_category_calc"],
                    row["price_per_std_unit_calc"], row["fx_rate_calc"], row["fx_rate_date_calc"],
                    row["price_usd_calc"], row["source_file"],
                )
                for _, row in clean_df.iterrows()
            ]
            cursor.executemany(
                """
                INSERT INTO price_clean
                    (adm0_name, adm1_name, mkt_name, cm_name, cur_name, pt_name,
                     mp_month, mp_year, mp_price, um_name, unit_category_calc,
                     price_per_std_unit_calc, fx_rate_calc, fx_rate_date_calc,
                     price_usd_calc, source_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                clean_params,
            )

        if not excluded_df.empty:
            excluded_params = [
                (
                    row.get("adm0_name"), row.get("adm1_name"), row.get("mkt_name"), row.get("cm_name"),
                    row.get("cur_name"), row.get("pt_name"), row.get("mp_month"), row.get("mp_year"),
                    row.get("mp_price"), row.get("um_name"), row["reason"], row["source_file"],
                )
                for _, row in excluded_df.iterrows()
            ]
            cursor.executemany(
                """
                INSERT INTO excluded_records
                    (adm0_name, adm1_name, mkt_name, cm_name, cur_name, pt_name,
                     mp_month, mp_year, mp_price, um_name, reason, source_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                excluded_params,
            )

        conn.commit()
        logging.info(f"SQL write committed: {len(clean_df)} clean rows, {len(excluded_df)} excluded rows.")

    except Exception:
        conn.rollback()
        logging.exception("SQL write failed - rolled back, previous month's data preserved untouched.")
        raise

    finally:
        conn.close()


@app.route(route="clean_pipeline", auth_level=func.AuthLevel.FUNCTION)
def clean_pipeline(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("clean_pipeline triggered.")
    try:
        wake_up_sql()

        conn_str = os.environ["STORAGE_CONNECTION_STRING"]
        blob_service = BlobServiceClient.from_connection_string(conn_str)

        update_fx_current(blob_service)

        raw_container = blob_service.get_container_client("raw")
        pattern = re.compile(r"^global_\d{8}\.csv$")
        blobs = [b for b in raw_container.list_blobs(name_starts_with="global_") if pattern.match(b.name)]
        if not blobs:
            return func.HttpResponse("No file matching pattern 'global_YYYYMMDD.csv' found in the 'raw' container.", status_code=404)
        latest_blob = max(blobs, key=lambda b: b.last_modified)
        logging.info(f"Reading raw file: {latest_blob.name}")

        raw_bytes = raw_container.download_blob(latest_blob.name).readall()
        df = pd.read_csv(io.BytesIO(raw_bytes))
        df = df[df["adm0_name"].isin(["Gambia", "Guinea-Bissau"])].copy()

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        filtered_container = blob_service.get_container_client("filtered")
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        filtered_blob_name = f"gm_gb_filtered_{date_str}.csv"
        filtered_container.upload_blob(name=filtered_blob_name, data=buf.getvalue(), overwrite=True)
        logging.info(f"Written: {filtered_blob_name} ({len(df)} rows)")

        fx_lookup = load_fx_lookup(blob_service)
        clean_df, excluded_df = clean_and_standardize(df, fx_lookup, filtered_blob_name)

        write_to_sql(clean_df, excluded_df)

        return func.HttpResponse(
            f"OK. Source: {latest_blob.name}. Filtered: {len(df)} rows. "
            f"Clean: {len(clean_df)} rows written to price_clean. "
            f"Excluded: {len(excluded_df)} rows written to excluded_records.",
            status_code=200
        )
    except Exception as e:
        logging.exception("clean_pipeline failed")
        return func.HttpResponse(f"ERROR: {type(e).__name__}: {str(e)}", status_code=500)
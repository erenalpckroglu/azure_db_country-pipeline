"""
PriceWatch GM-GW - Azure Function: clean_pipeline

Called by Azure Data Factory after the monthly raw file has been copied to Blob Storage.
Filters The Gambia and Guinea-Bissau, removes duplicates and invalid prices, standardises
units, converts prices to USD and writes the result to Azure SQL Database in one transaction.

All configuration comes from environment variables (Function App settings in Azure,
local.settings.json locally). No secrets are stored in this file.
"""
import io
import logging
import os
import re
import time
from datetime import datetime, timezone

import azure.functions as func
import pandas as pd
import pymssql
import requests
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

TARGET_COUNTRIES = ["Gambia", "Guinea-Bissau"]
RAW_CONTAINER, FILTERED_CONTAINER, FX_CONTAINER = "raw", "filtered", "fxrates"
RAW_FILE_PATTERN = re.compile(r"^global_\d{8}\.csv$")
FX_API_URL = "https://api.frankfurter.dev/v2/rates"

# Raw unit (um_name) -> (standard category, factor into the category's base unit).
# Units that are not listed are excluded with a reason instead of being guessed.
UNIT_MAP = {
    "KG": ("KG", 1.0),
    "125 G": ("KG", 0.125),
    "70 G": ("KG", 0.07),
    "200 G": ("KG", 0.2),
    "L": ("L", 1.0),
    "Unit": ("Unit", 1.0),
}

# One observation: one price for a commodity, unit and price type in a market and month.
BUSINESS_KEY_COLS = ["adm0_name", "adm1_name", "mkt_name", "cm_name", "pt_name", "um_name", "mp_month", "mp_year"]

CLEAN_COLS = [
    "adm0_name", "adm1_name", "mkt_name", "cm_name", "cur_name", "pt_name", "mp_month", "mp_year", "mp_price",
    "um_name", "unit_category_calc", "price_per_std_unit_calc", "fx_rate_calc", "fx_rate_date_calc",
    "price_usd_calc", "source_file",
]
EXCLUDED_COLS = [
    "adm0_name", "adm1_name", "mkt_name", "cm_name", "cur_name", "pt_name", "mp_month", "mp_year", "mp_price",
    "um_name", "reason", "source_file",
]

INSERT_CLEAN_SQL = """
    INSERT INTO price_clean
        (adm0_name, adm1_name, mkt_name, cm_name, cur_name, pt_name, mp_month, mp_year, mp_price, um_name,
         unit_category_calc, price_per_std_unit_calc, fx_rate_calc, fx_rate_date_calc, price_usd_calc, source_file)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_EXCLUDED_SQL = """
    INSERT INTO excluded_records
        (adm0_name, adm1_name, mkt_name, cm_name, cur_name, pt_name, mp_month, mp_year, mp_price, um_name,
         reason, source_file)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def connect_with_retry(autocommit: bool = True, max_attempts: int = 5, delay_seconds: int = 12):
    """Connect to the serverless SQL Database, retrying while it resumes from auto-pause
    (Azure answers with transient error 40613 until the database is available)."""
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
        except pymssql.Error as e:
            last_error = e
            logging.info("SQL connect attempt %d/%d failed (%s), retrying in %ds...",
                         attempt, max_attempts, type(e).__name__, delay_seconds)
            time.sleep(delay_seconds)
    raise last_error


def wake_up_sql() -> None:
    """Send a trivial query early, so the database resumes while the rest of the pipeline runs."""
    try:
        conn = connect_with_retry(max_attempts=2, delay_seconds=5)
        conn.cursor().execute("SELECT 1")
        conn.close()
        logging.info("wake_up_sql: SQL Database is awake.")
    except pymssql.Error:
        logging.info("wake_up_sql: initial ping failed, continuing - write_to_sql retries on connect.")


def update_fx_current(blob_service: BlobServiceClient) -> None:
    """Append this month's live USD rates for GMD and XOF to fx_rates_current.csv.

    Idempotent: if the month is already logged, nothing is fetched, so past months stay frozen.
    Only a missing file starts a new log; any other read error fails the run instead of
    overwriting the rate history.
    """
    container = blob_service.get_container_client(FX_CONTAINER)
    now = datetime.now(timezone.utc)
    try:
        current = pd.read_csv(io.BytesIO(container.download_blob("fx_rates_current.csv").readall()))
    except ResourceNotFoundError:
        current = pd.DataFrame(columns=["year", "month", "currency", "rate_lcu_per_usd", "fetched_date", "source"])

    if ((current["year"] == now.year) & (current["month"] == now.month)).any():
        logging.info("FX rate for %d-%02d already logged, skipping fetch.", now.year, now.month)
        return

    response = requests.get(FX_API_URL, params={"base": "USD", "quotes": "GMD,XOF"}, timeout=10)
    response.raise_for_status()
    new_rows = pd.DataFrame([
        {
            "year": now.year,
            "month": now.month,
            "currency": rec["quote"],
            "rate_lcu_per_usd": rec["rate"],
            "fetched_date": now.strftime("%Y-%m-%d"),
            "source": "frankfurter_latest",
        }
        for rec in response.json()
    ])
    updated = new_rows if current.empty else pd.concat([current, new_rows], ignore_index=True)
    container.upload_blob(name="fx_rates_current.csv", data=updated.to_csv(index=False), overwrite=True)
    logging.info("Logged FX rates for %d-%02d.", now.year, now.month)


def build_fx_lookup(historical: pd.DataFrame, live: pd.DataFrame | None = None) -> dict:
    """Merge historical and live monthly rates into one lookup:
    (year, month, currency) -> (local currency per USD, rate date). Live rates win on overlap."""
    month_start = [f"{y}-{m:02d}-01" for y, m in zip(historical["year"], historical["month"], strict=True)]
    frames = [historical.assign(rate_date=month_start)]
    if live is not None and not live.empty:
        frames.append(live.assign(rate_date=live["fetched_date"].astype(str)))
    rates = pd.concat(frames, ignore_index=True).drop_duplicates(["year", "month", "currency"], keep="last")
    return {
        (int(y), int(m), c): (float(r), d)
        for y, m, c, r, d in zip(
            rates["year"], rates["month"], rates["currency"], rates["rate_lcu_per_usd"], rates["rate_date"],
            strict=True,
        )
    }


def load_fx_lookup(blob_service: BlobServiceClient) -> dict:
    """Read both FX files from Blob Storage and build the lookup."""
    container = blob_service.get_container_client(FX_CONTAINER)
    historical = pd.read_csv(io.BytesIO(container.download_blob("fx_rates_historical.csv").readall()))
    try:
        live = pd.read_csv(io.BytesIO(container.download_blob("fx_rates_current.csv").readall()))
    except ResourceNotFoundError:
        logging.info("fx_rates_current.csv not found - using historical rates only.")
        live = None
    return build_fx_lookup(historical, live)


def clean_and_standardize(df: pd.DataFrame, fx_lookup: dict, source_file: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deduplicate, validate, standardise units and convert prices to USD.

    Returns (clean_df, excluded_df). Every excluded row keeps its original columns plus a
    reason, so nothing is dropped silently.
    """
    raw_cols = list(df.columns)
    excluded = []

    def exclude(frame: pd.DataFrame, mask: pd.Series, reason: str | pd.Series) -> pd.DataFrame:
        if mask.any():
            rows = frame.loc[mask, raw_cols].copy()
            rows["reason"] = reason[mask] if isinstance(reason, pd.Series) else reason
            rows["source_file"] = source_file
            excluded.append(rows)
        return frame.loc[~mask]

    # 1. Identical on business key + price + currency: a true duplicate, keep one copy.
    dup = df.duplicated(subset=BUSINESS_KEY_COLS + ["mp_price", "cur_name"], keep="first")
    df = exclude(df, dup, "duplicate on business key + price + currency - one copy kept")

    # 2. Same business key but a different price or currency: cannot be resolved automatically.
    variants = df.groupby(BUSINESS_KEY_COLS, dropna=False)[["mp_price", "cur_name"]].transform("nunique")
    conflict = (variants["mp_price"] > 1) | (variants["cur_name"] > 1)
    df = exclude(df, conflict, "duplicate business key with conflicting price or currency - needs manual review")

    # 3. Only positive prices are meaningful (this also catches missing prices).
    df = exclude(df, ~(df["mp_price"] > 0), "non-positive or missing price")

    # 4. Units: price per KG, L or Unit.
    category = df["um_name"].map({unit: cat for unit, (cat, _) in UNIT_MAP.items()})
    factor = df["um_name"].map({unit: f for unit, (_, f) in UNIT_MAP.items()})
    unknown = category.isna()
    df = exclude(df, unknown, "unknown unit '" + df["um_name"].astype(str) + "' - no conversion mapping")
    df = df.assign(unit_category_calc=category, price_per_std_unit_calc=df["mp_price"] / factor)

    # 5. Currency: the rate of the observation's own month.
    fx = pd.DataFrame(
        [(y, m, c, rate, date) for (y, m, c), (rate, date) in fx_lookup.items()],
        columns=["mp_year", "mp_month", "cur_name", "fx_rate_calc", "fx_rate_date_calc"],
    )
    df = df.merge(fx, on=["mp_year", "mp_month", "cur_name"], how="left")
    month = df["mp_month"].map(lambda m: f"{int(m):02d}" if pd.notna(m) else "??").astype(str)
    period = df["mp_year"].astype(str) + "-" + month
    df = exclude(df, df["fx_rate_calc"].isna(), "no FX rate available for " + df["cur_name"].astype(str) + " " + period)

    clean = df.assign(
        mp_month=df["mp_month"].astype(int),
        mp_year=df["mp_year"].astype(int),
        mp_price=df["mp_price"].astype(float),
        price_usd_calc=df["price_per_std_unit_calc"] / df["fx_rate_calc"],
        source_file=source_file,
    )[CLEAN_COLS].reset_index(drop=True)

    excluded_df = (
        pd.concat(excluded, ignore_index=True) if excluded
        else pd.DataFrame(columns=raw_cols + ["reason", "source_file"])
    )
    return clean, excluded_df


def _sql_rows(frame: pd.DataFrame, columns: list[str]) -> list[tuple]:
    """DataFrame -> tuples of plain Python values for executemany; missing values become NULL."""
    values = frame.reindex(columns=columns).astype(object)
    return list(values.where(values.notna(), None).itertuples(index=False, name=None))


def write_to_sql(clean_df: pd.DataFrame, excluded_df: pd.DataFrame) -> None:
    """Full refresh in one transaction: TRUNCATE + INSERT, committed only at the end.
    Any failure rolls everything back, so the last good dataset stays untouched."""
    conn = connect_with_retry(autocommit=False)
    cursor = conn.cursor()
    try:
        cursor.execute("TRUNCATE TABLE price_clean")
        cursor.execute("TRUNCATE TABLE excluded_records")
        if not clean_df.empty:
            cursor.executemany(INSERT_CLEAN_SQL, _sql_rows(clean_df, CLEAN_COLS))
        if not excluded_df.empty:
            cursor.executemany(INSERT_EXCLUDED_SQL, _sql_rows(excluded_df, EXCLUDED_COLS))
        conn.commit()
        logging.info("SQL write committed: %d clean rows, %d excluded rows.", len(clean_df), len(excluded_df))
    except Exception:
        conn.rollback()
        logging.exception("SQL write failed - rolled back, previous data preserved.")
        raise
    finally:
        conn.close()


@app.route(route="clean_pipeline", auth_level=func.AuthLevel.FUNCTION)
def clean_pipeline(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP entry point, called by Data Factory once the raw file is in Blob Storage."""
    logging.info("clean_pipeline triggered.")
    try:
        wake_up_sql()
        blob_service = BlobServiceClient.from_connection_string(os.environ["STORAGE_CONNECTION_STRING"])
        update_fx_current(blob_service)

        raw_container = blob_service.get_container_client(RAW_CONTAINER)
        blobs = [b for b in raw_container.list_blobs(name_starts_with="global_") if RAW_FILE_PATTERN.match(b.name)]
        if not blobs:
            return func.HttpResponse("No file matching 'global_YYYYMMDD.csv' in the raw container.", status_code=404)
        latest = max(blobs, key=lambda b: b.last_modified)
        logging.info("Reading raw file: %s", latest.name)

        raw = pd.read_csv(io.BytesIO(raw_container.download_blob(latest.name).readall()), low_memory=False)
        df = raw[raw["adm0_name"].isin(TARGET_COUNTRIES)].copy()

        filtered_name = f"gm_gb_filtered_{datetime.now(timezone.utc):%Y%m%d}.csv"
        blob_service.get_container_client(FILTERED_CONTAINER).upload_blob(
            name=filtered_name, data=df.to_csv(index=False), overwrite=True
        )
        logging.info("Written: %s (%d rows)", filtered_name, len(df))

        clean_df, excluded_df = clean_and_standardize(df, load_fx_lookup(blob_service), filtered_name)
        write_to_sql(clean_df, excluded_df)

        return func.HttpResponse(
            f"OK. Source: {latest.name}. Filtered: {len(df)} rows. "
            f"Clean: {len(clean_df)} rows. Excluded: {len(excluded_df)} rows.",
            status_code=200,
        )
    except Exception:
        logging.exception("clean_pipeline failed")
        return func.HttpResponse("Pipeline failed - see the function logs for details.", status_code=500)

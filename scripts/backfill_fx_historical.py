"""
One-time script: fetches monthly historical USD exchange rates for GMD (Gambia)
and XOF (Guinea-Bissau) from the Frankfurter API (free, no key) for 2006-2021
and uploads them as fx_rates_historical.csv to the 'fxrates' container.

Required environment variable: STORAGE_CONNECTION_STRING
"""
import io
import os

import pandas as pd
import requests
from azure.storage.blob import BlobServiceClient

START_DATE = "2006-01-01"
END_DATE = "2021-12-31"


def main() -> None:
    resp = requests.get(
        "https://api.frankfurter.dev/v2/rates",
        params={"base": "USD", "quotes": "GMD,XOF", "from": START_DATE, "to": END_DATE, "group": "month"},
        timeout=30,
    )
    resp.raise_for_status()

    # Response is a flat list: [{"date": "...", "base": "USD", "quote": "GMD", "rate": ...}, ...]
    rows = []
    for rec in resp.json():
        year, month, _ = rec["date"].split("-")
        rows.append({
            "year": int(year),
            "month": int(month),
            "currency": rec["quote"],
            "rate_lcu_per_usd": rec["rate"],
            "source": "frankfurter_monthly",
        })

    df = pd.DataFrame(rows).sort_values(["currency", "year", "month"]).reset_index(drop=True)
    print(f"Fetched {len(df)} monthly rates.")

    blob_service = BlobServiceClient.from_connection_string(os.environ["STORAGE_CONNECTION_STRING"])
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    blob_service.get_container_client("fxrates").upload_blob(
        name="fx_rates_historical.csv", data=buf.getvalue(), overwrite=True
    )
    print("Uploaded fx_rates_historical.csv to 'fxrates'.")


if __name__ == "__main__":
    main()

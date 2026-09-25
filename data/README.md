# Data

Offline copy of the pipeline outputs, exported with `scripts/export_to_local.py`
before the Azure subscription was closed.

| Folder | Content | In git |
|---|---|---|
| `curated/` | `price_clean` and `excluded_records` from Azure SQL, as Parquet and CSV | yes |
| `fx/` | Monthly exchange rates: `fx_rates_historical.csv`, `fx_rates_current.csv` | yes |
| `filtered/` | Country-filtered snapshot written by the pipeline | yes |
| `raw/` | Latest raw source file (about 280 MB) | no, local only |

For Power BI use `curated/price_clean.parquet`: numbers and dates keep their types,
so there are no decimal-separator issues. See [`../powerbi/OFFLINE_SETUP.md`](../powerbi/OFFLINE_SETUP.md).

## `price_clean` columns

| Column | Meaning |
|---|---|
| `id` | Row id from the SQL table |
| `adm0_name` / `adm1_name` / `mkt_name` | Country / region / market |
| `cm_name`, `pt_name` | Commodity, price type |
| `mp_month`, `mp_year` | Observation month and year |
| `mp_price`, `um_name`, `cur_name` | Original price, unit and currency |
| `unit_category_calc` | KG, L or Unit |
| `price_per_std_unit_calc` | Price per KG / L / Unit in local currency |
| `fx_rate_calc`, `fx_rate_date_calc` | Exchange rate used (local currency per USD) and its month |
| `price_usd_calc` | Final price in USD per standard unit |
| `source_file`, `ingested_at` | Lineage: source snapshot and load time |

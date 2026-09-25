# Food Price Monitoring Pipeline on Azure

An automated, serverless data pipeline on Microsoft Azure that collects monthly food price data for **The Gambia** and **Guinea-Bissau**, cleans and standardizes it, converts all prices to USD, loads it into Azure SQL Database and visualizes it in Power BI.

Team project for the *Big Data in Cloud* module, MSc Applied Information and Data Science, HSLU (2026).

> **Note:** The Azure resources ran on a course subscription that has since been closed, so the live pipeline is no longer reachable. This repository keeps the code, configuration, database schema, exported data and a guide to rebuild the Power BI report.

## Architecture

![Architecture](docs/images/architecture.png)

| Step | Service | What happens |
|---|---|---|
| 1. Schedule | Azure Data Factory | Pipeline runs automatically on the 15th of every month |
| 2. Ingest | Data Factory + Blob Storage | The source CSV is downloaded and stored unchanged as `global_yyyyMMdd.csv` |
| 3. Transform | Azure Functions (Python) | Filter countries, remove duplicates and invalid prices, standardize units, convert to USD |
| 4. Load | Azure SQL Database | Curated table replaced inside a single transaction |
| 5. Report | Power BI | Two interactive dashboards |
| 6. Monitor | Azure Monitor | Email alerts on pipeline failures and budget thresholds |

## Transformation logic

All transformation code is in [`function_app.py`](function_app.py).

- **Country filter:** keeps only Gambia and Guinea-Bissau.
- **Duplicates:** identical records are kept once. Records with the same key but a different price or currency are excluded and logged with a reason.
- **Invalid prices:** zero or missing prices are excluded and logged with a reason.
- **Units:** 70 g, 125 g and 200 g packages are converted to price per KG. Litres and pieces stay in their own categories.
- **Currency:** GMD and XOF are converted to USD with the exchange rate of the observation's own month. Historical rates were loaded once; the current month's rate is fetched on every run from the Frankfurter API.
- **Safe load:** the SQL write runs in one transaction. If anything fails, it rolls back and the previous data stays intact.
- **Serverless database handling:** the function wakes the auto-paused database early and retries the connection on transient errors.

The code was refactored after the course: vectorised pandas instead of row loops, narrower error handling, and the invalid-price rule. A test rebuilds the curated table from the committed snapshot. All 76,766 rows match the table the deployed function wrote; the only difference is one zero price that the current code excludes. The deployed version is in the first commit.

## Dashboards

**Seasonal price analysis:** average USD price by month of year; hovering shows the exchange rate used.

![Seasonal dashboard](docs/images/dashboard_seasonal.png)

**Price trend over time:** the same metrics on a continuous timeline.

![Trend dashboard](docs/images/dashboard_trend.png)

Filters: country, region, market, commodity, unit category, price type, year range.
KPI cards: month-over-month and year-over-year change, Gambia vs Guinea-Bissau price gap, record count, data freshness, share of missing months per country.

## Key findings

Computed from [`data/curated/price_clean.parquet`](data/curated/price_clean.parquet) (76,766 observations with a positive price).

- **Like for like, food is cheaper in The Gambia.** For the same commodity, unit, price type and month (752 matches across 20 commodities, 2007–2021), Gambian prices were a median **35% lower** than in Guinea-Bissau, and lower in 84% of the matches. Local cereals cost about half (millet −54%, sorghum −55%, maize −43%); fonio (+20%) and shelled groundnuts (+17%) are the exceptions.
- **Averages over different baskets mislead.** With the dashboard's filters (2016–2021, retail, per kg), the average over all commodities puts the gap at only −6.8%, while the like-for-like median is −29.5%. The two countries report different commodities, so comparisons need matching ones.
- **Guinea-Bissau's 2020 "price jump" is a coverage change.** WFP expanded Guinea-Bissau from 9 to 39 tracked commodities in 2020 (278 to 5,187 retail per-kg records). The all-commodity average rose 77%, but a fixed basket of the 9 commodities reported in both years fell 13% (1.05 to 0.91 USD per kg).

## Monitoring

![Monitoring and alerts](docs/images/monitoring_and_alerts.png)

Budget thresholds and failed pipeline runs both notify the same Action Group by email.

## More visuals

**Planned Phase 2:** a lakehouse on Azure Data Lake Storage and Databricks (bronze, silver, gold) with CPI and daily commodity prices as additional sources.

![Phase 2 lakehouse architecture](docs/images/presentation/phase2_lakehouse_architecture.png)

**System features** defined in the requirements specification:

![System features](docs/images/presentation/system_features_overview.png)

## Repository structure

```
.
├── function_app.py   Azure Function: transformation and SQL load (Python)
├── host.json         Azure Functions host configuration
├── requirements.txt  Function dependencies
├── adf/              Data Factory pipeline (ARM template export)
├── sql/              Database schema
├── powerbi/          Power BI theme and rebuild guide (all DAX)
├── data/             Offline export of the pipeline outputs
├── scripts/          FX backfill and local export scripts
├── tests/            Unit and end-to-end tests for the transformation logic
└── docs/             Images, project documents, requirements traceability
```

## Project documents

- [`PriceShield_Gambia_MBR.pdf`](docs/project/PriceShield_Gambia_MBR.pdf) - final presentation: context, requirements, architecture, dashboards and service levels.
- [`PriceShield_Requirements.pdf`](docs/project/PriceShield_Requirements.pdf) - system features and functional requirements (the basis of the traceability table).
- [`PriceShield_Service_Level_Agreement.pdf`](docs/project/PriceShield_Service_Level_Agreement.pdf) - service scope, responsibilities, availability and support targets.
- [`PriceShield_Cost_Estimate.pdf`](docs/project/PriceShield_Cost_Estimate.pdf) - Azure pricing estimates for Phase 1 and Phase 2 and a comparison of dashboard tools.

## Run the tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Unit tests cover each cleaning rule; an end-to-end test rebuilds the curated table from the committed data.

## Data source

This project uses the **Global Food Prices Database**, shared by the **World Food Programme (WFP)** on the Humanitarian Data Exchange: <https://data.humdata.org/dataset/wfp-food-prices>. The pipeline downloads the global file (about 2 million monthly market prices from 98 countries, 1990–2021) and keeps The Gambia and Guinea-Bissau. License: [CC BY-IGO](https://creativecommons.org/licenses/by/3.0/igo/legalcode), data © WFP.

Exchange rates: [Frankfurter API](https://frankfurter.dev) (monthly USD rates for GMD and XOF).

## Limitations and next steps

- **Frozen source:** the WFP global file on HDX has not been updated since August 2021, so the data ends in July 2021. WFP now publishes monthly updated country datasets ([Gambia](https://data.humdata.org/dataset/wfp-food-prices-for-gambia), [Guinea-Bissau](https://data.humdata.org/dataset/wfp-food-prices-for-guinea-bissau)) with a different column layout; switching the source is the main next step.
- The dashboard's country gap averages over different commodity baskets; a matched-basket measure (see Key findings) would be more robust.
- Full reload each month suits the current volume; a much larger source would need incremental loading.
- Infrastructure was created in the Azure Portal; infrastructure as code (Bicep) would make it redeployable.
- Planned extension: Azure Data Lake Storage and Databricks for forecasting, with CPI and daily commodity prices as reference series.

Requirement coverage: [`docs/requirements_traceability.md`](docs/requirements_traceability.md).

To open the dashboards without Azure, see [`powerbi/OFFLINE_SETUP.md`](powerbi/OFFLINE_SETUP.md).

## License

Code: [MIT](LICENSE). Data: derived from the WFP Global Food Prices Database (CC BY-IGO, © WFP). Project documents and images: © the project team.

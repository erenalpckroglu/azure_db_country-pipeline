# Requirements traceability

The project started from a requirements specification with five features and 30 functional requirements.
This table shows what the current MVP actually covers. "Partial" and "Not implemented" are deliberate scope decisions, not oversights.

| Requirement | Status | How it is met / what is missing |
|---|---|---|
| **1. Automated data processing** | | |
| ADP-01 Automated ingestion | Done | ADF schedule trigger, monthly |
| ADP-02 Raw data preservation | Done | Raw file stored unchanged in Blob Storage as `global_yyyyMMdd.csv`, never overwritten |
| ADP-03 Country filtering | Done | Only Gambia and Guinea-Bissau reach the curated table |
| ADP-04 Processing deadline (24 h) | Partial | Runs on a fixed schedule; time since source publication is not measured |
| ADP-05 Failed-load protection | Done | TRUNCATE + INSERT in one SQL transaction, rolled back on any failure |
| **2. Reliable and comparable data** | | |
| RCD-01 Data validation | Partial | Types enforced by schema; no explicit range rules yet |
| RCD-02 Missing vs invalid data | Partial | Missing months measured per country in the dashboard; non-positive prices are not yet quarantined |
| RCD-03 Duplicate prevention | Done | Exact duplicates kept once; conflicting price or currency on the same business key excluded for review |
| RCD-04 Unit standardisation | Done | 70 g / 125 g / 200 g packages converted to price per KG; original unit and factor retained |
| RCD-05 Currency standardisation | Done | Original price, currency, applied rate, rate date and USD value all stored |
| RCD-06 Invalid comparison handling | Done | Every excluded row stored in `excluded_records` with a reason |
| **3. Price monitoring and country comparison** | | |
| MON-01 National price trends | Done | Two dashboards (seasonal and chronological) |
| MON-02 Regional drill-down | Done | Separate region filters per country, plus market filter |
| MON-03 Comparable observations | Partial | Unit category and price type filters; enforced by the user's filter choice, not by the model |
| MON-04 Relative price changes | Done | Month-over-month and year-over-year cards, with the compared months shown |
| MON-05 Country price difference | Done | Gambia vs Guinea-Bissau price gap card |
| MON-06 Data coverage | Done | Record count, % missing months per country, most recent data month |
| **4. Forecasting and early warning** | | |
| FEW-01 to FEW-06 | Not implemented | Planned for Phase 2 (Databricks) |
| **5. Secure decision reporting** | | |
| REP-01 Interactive filtering | Done | All visuals respond to the same slicers |
| REP-02 Data freshness | Done | "Pipeline last updated" and "Most recent data month" cards |
| REP-03 Controlled export | Partial | Power BI's built-in PDF/PowerPoint export; no custom export layout |
| REP-04 Report performance | Partial | Import mode, so interactions do not hit the database; not formally measured |
| REP-05 Authenticated access | Out of scope here | Handled at the Power BI service / tenant level |

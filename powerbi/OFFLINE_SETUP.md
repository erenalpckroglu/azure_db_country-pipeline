# Power BI - offline setup

Everything needed to open, refresh or rebuild the dashboards **without Azure**, using the exported files in `data/curated/`.

## Option A - open the existing report and point it to the local file

Use this if you have the original `PriceWatch.pbix`. It is not included in this repository because it contains the connection details of the original database.

1. Open `PriceWatch.pbix`. The data is stored inside the file (Import mode), so it opens even without Azure.
2. To make **Refresh** work offline: Home -> Transform data -> select the `price_clean` query -> Advanced Editor.
3. Replace only the first two steps (the `Sql.Database(...)` line and the `#"Navigation 1"` line after it) with:

```m
Source = Parquet.Document(File.Contents("C:\path\to\repo\data\curated\price_clean.parquet")),
#"Navigation 1" = Source,
```

4. Keep the name of the second step exactly as it is (`#"Navigation 1"`), so the next step, `#"Renamed Columns"` (`fx_rate_calc` -> `Exchange Rate`, `fx_rate_date_calc` -> `Exchange Rate Date`), and every measure keep working.
5. Close & Apply, then Refresh.

## Option B - rebuild from scratch

### 1. Load the data

1. Power BI Desktop -> Get data -> **Parquet** -> select `data/curated/price_clean.parquet` -> Load.
   Parquet keeps numbers and dates typed, so there are no decimal-separator problems on a non-English Windows locale.
2. If you use the CSV instead: Get data -> Text/CSV -> Transform Data, then for every numeric and date column use
   **Change Type -> Using Locale -> English (United States)**. Otherwise `1.55` can be read as `155`.
3. Rename the query to `price_clean` (all formulas below use this name and the original column names).
4. View -> Themes -> Browse for themes -> `powerbi/PriceWatch_theme.json`.

### 2. Calculated column

Table tools -> New column:

```dax
ObservationDate = DATE(price_clean[mp_year], price_clean[mp_month], 1)
```

Set its format to `yyyy-MM` (Column tools -> Format -> Custom). This avoids localized month names.

### 3. Measures

Table tools -> New measure, one per block.

```dax
Avg Price USD = AVERAGE(price_clean[price_usd_calc])
```

```dax
FX Rate Used = FORMAT(AVERAGE(price_clean[fx_rate_calc]), "0.00")
```

```dax
Total Price Records = COUNTROWS(price_clean)
```

```dax
Most Recent Data Month = MAX(price_clean[ObservationDate])
```

```dax
Pipeline Last Updated = MAX(price_clean[ingested_at])
```

```dax
Price Change (vs Last Month) =
VAR MaxDate = CALCULATE(MAX(price_clean[ObservationDate]), ALLSELECTED(price_clean))
VAR PrevDate = EDATE(MaxDate, -1)
VAR CurrentVal = CALCULATE([Avg Price USD], price_clean[ObservationDate] = MaxDate)
VAR PrevVal = CALCULATE([Avg Price USD], ALL(price_clean[ObservationDate]), price_clean[ObservationDate] = PrevDate)
RETURN DIVIDE(CurrentVal - PrevVal, PrevVal)
```

```dax
Price Change (vs Last Year) =
VAR MaxDate = CALCULATE(MAX(price_clean[ObservationDate]), ALLSELECTED(price_clean))
VAR PrevDate = EDATE(MaxDate, -12)
VAR CurrentVal = CALCULATE([Avg Price USD], price_clean[ObservationDate] = MaxDate)
VAR PrevVal = CALCULATE([Avg Price USD], ALL(price_clean[ObservationDate]), price_clean[ObservationDate] = PrevDate)
RETURN DIVIDE(CurrentVal - PrevVal, PrevVal)
```

```dax
MoM Comparison Label =
VAR MaxDate = CALCULATE(MAX(price_clean[ObservationDate]), ALLSELECTED(price_clean))
RETURN FORMAT(MaxDate, "yyyy-MM") & " vs " & FORMAT(EDATE(MaxDate, -1), "yyyy-MM")
```

```dax
YoY Comparison Label =
VAR MaxDate = CALCULATE(MAX(price_clean[ObservationDate]), ALLSELECTED(price_clean))
RETURN FORMAT(MaxDate, "yyyy-MM") & " vs " & FORMAT(EDATE(MaxDate, -12), "yyyy-MM")
```

```dax
Gambia vs Guinea-Bissau Price Gap =
VAR GM = CALCULATE([Avg Price USD], price_clean[adm0_name] = "Gambia")
VAR GB = CALCULATE([Avg Price USD], price_clean[adm0_name] = "Guinea-Bissau")
RETURN DIVIDE(GM - GB, GB)
```

```dax
% Missing Months (Gambia) =
VAR MinD = CALCULATE(MIN(price_clean[ObservationDate]), ALL(price_clean[adm0_name]), price_clean[adm0_name] = "Gambia")
VAR MaxD = CALCULATE(MAX(price_clean[ObservationDate]), ALL(price_clean[adm0_name]), price_clean[adm0_name] = "Gambia")
VAR Expected = DATEDIFF(MinD, MaxD, MONTH) + 1
VAR Actual = CALCULATE(DISTINCTCOUNT(price_clean[ObservationDate]), ALL(price_clean[adm0_name]), price_clean[adm0_name] = "Gambia")
RETURN DIVIDE(Expected - Actual, Expected)
```

```dax
% Missing Months (Guinea-Bissau) =
VAR MinD = CALCULATE(MIN(price_clean[ObservationDate]), ALL(price_clean[adm0_name]), price_clean[adm0_name] = "Guinea-Bissau")
VAR MaxD = CALCULATE(MAX(price_clean[ObservationDate]), ALL(price_clean[adm0_name]), price_clean[adm0_name] = "Guinea-Bissau")
VAR Expected = DATEDIFF(MinD, MaxD, MONTH) + 1
VAR Actual = CALCULATE(DISTINCTCOUNT(price_clean[ObservationDate]), ALL(price_clean[adm0_name]), price_clean[adm0_name] = "Guinea-Bissau")
RETURN DIVIDE(Expected - Actual, Expected)
```

The original `.pbix` also contains four helper measures that no visual uses (`Expected Months`, `% Missing Months`, `Country Color`, `Price with FX Rate`); they are not needed for a rebuild.

Formats (Measure tools -> Format):

| Measure | Format |
|---|---|
| Price Change (vs Last Month), Price Change (vs Last Year), Gambia vs Guinea-Bissau Price Gap, % Missing Months (both) | Percentage, 1 decimal |
| Most Recent Data Month | Date, `dd.MM.yyyy` |
| Pipeline Last Updated | Custom, `dd.MM.yyyy HH:mm` |
| Avg Price USD | Decimal, 2 decimals |

### 4. Page 1 - Seasonal Price Analysis

| Element | Setting |
|---|---|
| Line chart | X-axis `mp_month`, Y-axis `Avg Price USD`, Legend `adm0_name`, Tooltips `FX Rate Used` |
| Title | Avg USD Price Per Unit and FX Rate Used |

### 5. Page 2 - Price Trend Over Time

| Element | Setting |
|---|---|
| Line chart | X-axis `ObservationDate` (choose the field itself, not *Date Hierarchy*), Y-axis `Avg Price USD`, Legend `adm0_name`, Tooltips `FX Rate Used` |
| X-axis format | Custom `yyyy-MM` |
| Title | Average USD Price Over Time by Country |

### 6. Slicers (same on both pages)

| Slicer | Field | Style |
|---|---|---|
| Country | `adm0_name` | Vertical list, multi-select |
| Gambia Regions | `adm1_name`, visual filter `adm0_name = Gambia` | Dropdown |
| Guinea-Bissau Regions | `adm1_name`, visual filter `adm0_name = Guinea-Bissau` | Dropdown |
| Year | `mp_year` | Between |
| Market | `mkt_name` | Dropdown |
| Commodity | `cm_name` | Dropdown |
| Unit Category | `unit_category_calc` | Dropdown (select KG for price per kg) |
| Price Type | `pt_name` | Dropdown |

### 7. KPI cards (same on both pages)

Top row: `Price Change (vs Last Month)` with `MoM Comparison Label` below it, `Price Change (vs Last Year)` with `YoY Comparison Label` below it, `Gambia vs Guinea-Bissau Price Gap`, `Total Price Records`.

Bottom row: `Most Recent Data Month`, `Pipeline Last Updated`, `% Missing Months (Gambia)`, `% Missing Months (Guinea-Bissau)`.

Tip: style one card, then use **Format Painter** to copy the style to the others.

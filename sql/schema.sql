-- PriceWatch GM-GW - Azure SQL Database schema
-- Columns ending in _calc are computed by the pipeline; all others are copied from the source.

CREATE TABLE price_clean (
    id INT IDENTITY(1,1) PRIMARY KEY,
    adm0_name NVARCHAR(50) NOT NULL,          -- country
    adm1_name NVARCHAR(100) NULL,             -- region
    mkt_name NVARCHAR(100) NOT NULL,          -- market
    cm_name NVARCHAR(150) NOT NULL,           -- commodity
    cur_name NVARCHAR(10) NOT NULL,           -- original currency (GMD / XOF)
    pt_name NVARCHAR(50) NOT NULL,            -- price type
    mp_month INT NOT NULL,
    mp_year INT NOT NULL,
    mp_price DECIMAL(18,4) NOT NULL,          -- original price, original unit
    um_name NVARCHAR(20) NOT NULL,            -- original unit
    unit_category_calc NVARCHAR(10) NOT NULL, -- KG / L / Unit
    price_per_std_unit_calc DECIMAL(18,6) NOT NULL,
    fx_rate_calc DECIMAL(18,6) NOT NULL,      -- local currency per 1 USD
    fx_rate_date_calc DATE NOT NULL,
    price_usd_calc DECIMAL(18,6) NOT NULL,
    source_file NVARCHAR(200) NOT NULL,
    ingested_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE excluded_records (
    id INT IDENTITY(1,1) PRIMARY KEY,
    adm0_name NVARCHAR(50) NULL,
    adm1_name NVARCHAR(100) NULL,
    mkt_name NVARCHAR(100) NULL,
    cm_name NVARCHAR(150) NULL,
    cur_name NVARCHAR(10) NULL,
    pt_name NVARCHAR(50) NULL,
    mp_month INT NULL,
    mp_year INT NULL,
    mp_price DECIMAL(18,4) NULL,
    um_name NVARCHAR(20) NULL,
    reason NVARCHAR(300) NOT NULL,            -- why the row was excluded
    source_file NVARCHAR(200) NULL,
    excluded_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

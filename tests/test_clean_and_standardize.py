"""Tests for the pure transformation logic (no Azure resources needed)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from function_app import CLEAN_COLS, build_fx_lookup, clean_and_standardize  # noqa: E402

FX = {
    (2020, 1, "GMD"): (50.0, "2020-01-01"),
    (2020, 1, "XOF"): (600.0, "2020-01-01"),
}


def row(**overrides):
    base = {
        "adm0_name": "Gambia", "adm1_name": "Banjul", "mkt_name": "Banjul",
        "cm_name": "Maize - Retail", "cur_name": "GMD", "pt_name": "Retail",
        "um_name": "KG", "mp_month": 1, "mp_year": 2020, "mp_price": 25.0,
    }
    base.update(overrides)
    return base


def run(rows):
    return clean_and_standardize(pd.DataFrame(rows), FX, "test.csv")


def test_kg_price_converted_to_usd():
    clean, excluded = run([row()])
    assert excluded.empty
    assert clean.loc[0, "price_usd_calc"] == pytest.approx(25.0 / 50.0)
    assert clean.loc[0, "unit_category_calc"] == "KG"


def test_gram_package_standardized_to_kg():
    clean, _ = run([row(um_name="70 G", mp_price=5.0)])
    assert clean.loc[0, "unit_category_calc"] == "KG"
    assert clean.loc[0, "price_per_std_unit_calc"] == pytest.approx(5.0 / 0.07)
    assert clean.loc[0, "price_usd_calc"] == pytest.approx((5.0 / 0.07) / 50.0)


def test_litre_and_unit_are_not_converted_to_kg():
    clean, _ = run([row(um_name="L", cm_name="Oil"), row(um_name="Unit", cm_name="Eggs")])
    assert set(clean["unit_category_calc"]) == {"L", "Unit"}


def test_exact_duplicate_keeps_one_copy():
    clean, excluded = run([row(), row()])
    assert len(clean) == 1
    assert len(excluded) == 1
    assert "one copy kept" in excluded.loc[0, "reason"]


def test_conflicting_price_excludes_whole_group():
    clean, excluded = run([row(mp_price=25.0), row(mp_price=30.0)])
    assert clean.empty
    assert len(excluded) == 2
    assert all("conflicting" in r for r in excluded["reason"])


def test_conflicting_currency_excludes_whole_group():
    clean, excluded = run([row(cur_name="GMD"), row(cur_name="XOF")])
    assert clean.empty
    assert len(excluded) == 2


def test_missing_fx_rate_is_excluded_with_reason():
    clean, excluded = run([row(mp_year=2019)])
    assert clean.empty
    assert "no FX rate" in excluded.loc[0, "reason"]


def test_unknown_unit_is_excluded_with_reason():
    clean, excluded = run([row(um_name="5 KG")])
    assert clean.empty
    assert "unknown unit" in excluded.loc[0, "reason"]


def test_non_positive_price_is_excluded_with_reason():
    clean, excluded = run([row(mp_price=0.0), row(mkt_name="Serekunda", mp_price=-1.0)])
    assert clean.empty
    assert len(excluded) == 2
    assert all("non-positive" in r for r in excluded["reason"])


def test_live_fx_rate_overrides_historical():
    historical = pd.DataFrame({"year": [2020], "month": [1], "currency": ["GMD"], "rate_lcu_per_usd": [50.0]})
    live = pd.DataFrame({"year": [2020], "month": [1], "currency": ["GMD"], "rate_lcu_per_usd": [52.0],
                         "fetched_date": ["2020-01-20"]})
    assert build_fx_lookup(historical) == {(2020, 1, "GMD"): (50.0, "2020-01-01")}
    assert build_fx_lookup(historical, live) == {(2020, 1, "GMD"): (52.0, "2020-01-20")}


def test_reproduces_deployed_output_on_real_data():
    """The committed snapshot + FX files give the table the deployed function wrote to Azure SQL,
    except the one zero price that the stricter current code excludes."""
    data = ROOT / "data"
    filtered = pd.read_parquet(data / "filtered" / "gm_gb_filtered_20260923.parquet")
    fx = build_fx_lookup(pd.read_csv(data / "fx" / "fx_rates_historical.csv"),
                         pd.read_csv(data / "fx" / "fx_rates_current.csv"))
    clean, excluded = clean_and_standardize(filtered, fx, "gm_gb_filtered_20260923.csv")

    deployed = pd.read_parquet(data / "curated" / "price_clean.parquet")
    deployed["fx_rate_date_calc"] = deployed["fx_rate_date_calc"].dt.strftime("%Y-%m-%d")
    assert list(excluded["reason"]) == ["non-positive or missing price"]
    deployed = deployed[deployed["price_usd_calc"] > 0]

    key = ["adm0_name", "adm1_name", "mkt_name", "cm_name", "pt_name", "um_name", "mp_year", "mp_month"]
    ours = clean.sort_values(key).reset_index(drop=True)
    theirs = deployed[CLEAN_COLS].sort_values(key).reset_index(drop=True)
    assert len(ours) == len(theirs) == 76_766
    for col in CLEAN_COLS:
        if col in ("mp_price", "price_per_std_unit_calc", "fx_rate_calc", "price_usd_calc"):
            assert np.allclose(ours[col], theirs[col], rtol=1e-6, atol=1e-4), col   # SQL DECIMAL rounding
        else:
            assert (ours[col].astype(str).to_numpy() == theirs[col].astype(str).to_numpy()).all(), col

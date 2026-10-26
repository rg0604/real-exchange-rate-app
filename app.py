"""IMF bilateral real-exchange-rate explorer. Python 3.12 / Streamlit."""
from __future__ import annotations

from datetime import date
from io import StringIO
import re

import altair as alt
import numpy as np
import pandas as pd
import pycountry
import requests
import streamlit as st

API_ROOT = "https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA"
FIRST_MONTH = pd.Period("1976-01", freq="M")
# Selectable IMF member economies are country ISO3 entries; series availability varies.
# G163 is the IMF's Euro area aggregate, not an ISO3 country.
EXTRA_AREAS = {"G163": "Euro area"}

st.set_page_config(page_title="IMF Real Exchange Rate Explorer", page_icon="🌐", layout="wide")


def available_areas() -> dict[str, str]:
    """ISO3 countries + IMF Euro area; data coverage is verified at query time."""
    countries = {
        c.alpha_3: c.name
        for c in pycountry.countries
        if hasattr(c, "alpha_3")
    }
    countries.update(EXTRA_AREAS)
    return dict(sorted(countries.items(), key=lambda item: item[1]))


AREAS = available_areas()


def api_csv(flow: str, key: str) -> pd.DataFrame:
    """Request IMF SDMX 3.0 CSV; preserve errors rather than misreport empty data."""
    url = f"{API_ROOT}/{flow}/~/{key}"
    headers = {"Accept": "text/csv", "User-Agent": "IMF-RER-Explorer/1.0"}
    params = {"c[TIME_PERIOD]": "ge:1976-M01"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=(10, 65))
        if response.status_code in (404, 422):
            return pd.DataFrame()
        response.raise_for_status()
        body = response.text.lstrip("\ufeff").strip()
        if not body:
            return pd.DataFrame()
        # IMF SDMX errors can come as an XML/JSON body or an HTML response.
        if body.startswith(("<", "{")):
            raise ValueError(f"IMF returned non-CSV data for {flow}: {body[:160]}")
        return pd.read_csv(StringIO(body), low_memory=False)
    except (requests.RequestException, pd.errors.ParserError, ValueError) as exc:
        raise RuntimeError(f"IMF {flow} request failed: {exc}") from exc


def tidy_monthly(frame: pd.DataFrame, code: str) -> pd.Series:
    """Extract the target country's monthly observations from IMF CSV."""
    if frame.empty:
        return pd.Series(dtype="float64", name=code)
    frame = frame.copy()
    frame.columns = [str(c).strip().upper() for c in frame.columns]
    required = {"TIME_PERIOD", "OBS_VALUE"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Unexpected IMF CSV columns: {', '.join(frame.columns)}")
    if "COUNTRY" in frame.columns:
        frame = frame.loc[frame["COUNTRY"].astype(str).str.upper() == code]
    if "FREQUENCY" in frame.columns:
        frame = frame.loc[frame["FREQUENCY"].astype(str).str.upper() == "M"]
    if frame.empty:
        return pd.Series(dtype="float64", name=code)
    periods = frame["TIME_PERIOD"].astype(str).str.strip().str.replace(
        r"^(\d{4})-M(\d{2})$", r"\1-\2", regex=True
    )
    valid = periods.str.fullmatch(r"\d{4}-\d{2}")
    frame = frame.loc[valid].copy()
    periods = periods.loc[valid]
    if frame.empty:
        return pd.Series(dtype="float64", name=code)
    frame["MONTH"] = pd.PeriodIndex(periods, freq="M")
    frame["VALUE"] = pd.to_numeric(frame["OBS_VALUE"], errors="coerce")
    # Do not silently average repeated observations of the same series/month.
    series = frame.drop_duplicates(subset=["MONTH"], keep="last").set_index("MONTH")["VALUE"]
    return series.sort_index().rename(code).astype("float64")


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_cpi(code: str) -> pd.Series:
    raw = api_csv("CPI", f"{code}.CPI._T.IX.M")
    return tidy_monthly(raw, code)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def get_usd_rate(code: str) -> pd.Series:
    # Quote is 'units of domestic currency per US dollar'.
    # United States serves as a unit-valued numeraire for all months.
    if code == "USA":
        return pd.Series(dtype="float64", name=code)
    raw = api_csv("ER", f"{code}.XDC_USD.PA_RT.M")
    return tidy_monthly(raw, code)


def calculate(code1: str, code2: str, cpi1: pd.Series, cpi2: pd.Series,
              usd1: pd.Series, usd2: pd.Series, start: pd.Period,
              end: pd.Period) -> tuple[pd.DataFrame, float, int]:
    """S = (currency2/USD)/(currency1/USD); RER = S*CPI1/CPI2."""
    months = pd.period_range(start, end, freq="M")
    df = pd.DataFrame(index=months)
    df.index.name = "Month"
    df["CPI_1"] = cpi1.reindex(months)
    df["CPI_2"] = cpi2.reindex(months)
    df["XDC_per_USD_1"] = (1.0 if code1 == "USA" else usd1.reindex(months))
    df["XDC_per_USD_2"] = (1.0 if code2 == "USA" else usd2.reindex(months))
    # Zero or negative CPIs / exchange rates are economically invalid inputs.
    for col in ("CPI_1", "CPI_2", "XDC_per_USD_1", "XDC_per_USD_2"):
        df.loc[~np.isfinite(df[col]) | (df[col] <= 0), col] = np.nan
    df["S_currency2_per_currency1"] = df["XDC_per_USD_2"] / df["XDC_per_USD_1"]
    df["RER"] = df["S_currency2_per_currency1"] * df["CPI_1"] / df["CPI_2"]
    valid = df["RER"].dropna()
    count = len(valid)
    benchmark = float(valid.mean()) if count else np.nan
    df["Real_Index"] = 100 * df["RER"] / benchmark if count and benchmark > 0 else np.nan
    df["Valid_RER_month"] = df["RER"].notna()
    return df, benchmark, count


def chart_for(df: pd.DataFrame, mode: str, pair: str) -> alt.Chart:
    visual = df.reset_index().copy()
    visual["Date"] = visual["Month"].dt.to_timestamp()
    shared = alt.Chart(visual).encode(
        x=alt.X("Date:T", title="Month", axis=alt.Axis(format="%Y")),
    )
    real = shared.mark_line(color="#2563eb", strokeWidth=2).encode(
        y=alt.Y("Real_Index:Q", title="Real exchange rate index (sample mean = 100)", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("Date:T", title="Month", format="%Y-%m"),
                 alt.Tooltip("Real_Index:Q", title="Real index", format=".3f")],
    )
    nominal = shared.mark_line(color="#ea580c", strokeWidth=2).encode(
        y=alt.Y("S_currency2_per_currency1:Q", title=f"Nominal S ({pair})", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("Date:T", title="Month", format="%Y-%m"),
                 alt.Tooltip("S_currency2_per_currency1:Q", title="Nominal S", format=".6g")],
    )
    if mode == "Real":
        return real.properties(height=440).interactive()
    if mode == "Nominal":
        return nominal.properties(height=440).interactive()
    return alt.layer(real, nominal).resolve_scale(y="independent").properties(height=440).interactive()


def export_csv(df: pd.DataFrame, benchmark: float) -> bytes:
    out = df.copy()
    out.insert(len(out.columns), "Sample_Mean_RER", benchmark)
    out.index = out.index.astype(str)
    return out.to_csv(index=True, float_format="%.12g", na_rep="").encode("utf-8")


st.title("🌐 IMF Real Exchange Rate Explorer")
st.caption("Monthly bilateral real and nominal exchange rates · IMF CPI and ER datasets · 1976 onward, where available")

with st.sidebar:
    st.header("Selection")
    labels = {code: f"{name} ({code})" for code, name in AREAS.items()}
    codes = list(AREAS)
    first_default = codes.index("USA")
    second_default = codes.index("IND")
    country1 = st.selectbox("Country 1 (base currency)", codes, index=first_default,
                            format_func=lambda c: labels[c])
    country2 = st.selectbox("Country 2 (quote currency)", codes, index=second_default,
                            format_func=lambda c: labels[c])
    st.caption("S is Country 2 currency units per 1 Country 1 currency unit.")
    start_date = st.date_input("Start month", value=date(2000, 1, 1), min_value=date(1976, 1, 1),
                               max_value=date.today().replace(day=1), format="YYYY/MM/DD")
    end_date = st.date_input("End month", value=date.today().replace(day=1),
                             min_value=date(1976, 1, 1), max_value=date.today().replace(day=1),
                             format="YYYY/MM/DD")
    mode = st.radio("Chart series", ["Both", "Real", "Nominal"], horizontal=True)
    if st.button("Refresh cached IMF data", use_container_width=True):
        get_cpi.clear()
        get_usd_rate.clear()
        st.rerun()

start, end = pd.Period(start_date, freq="M"), pd.Period(end_date, freq="M")
if country1 == country2:
    st.warning("Select two different countries or areas.")
    st.stop()
if start < FIRST_MONTH or end < start:
    st.error("Choose a valid period starting January 1976, with the start month no later than the end month.")
    st.stop()

with st.spinner("Fetching monthly IMF observations…"):
    try:
        cpi1 = get_cpi(country1)
        cpi2 = get_cpi(country2)
        usd1 = get_usd_rate(country1)
        usd2 = get_usd_rate(country2)
        result, rer_mean, n_valid = calculate(country1, country2, cpi1, cpi2, usd1, usd2, start, end)
    except (RuntimeError, ValueError) as exc:
        st.error(f"Unable to retrieve or interpret IMF data: {exc}")
        st.info("Try again later, refresh the cache, or select countries with better IMF coverage. No figures have been imputed.")
        st.stop()

st.subheader(f"{AREAS[country1]} → {AREAS[country2]}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Valid RER months", f"{n_valid:,} / {len(result):,}")
c2.metric("Sample-average RER (RER*)", f"{rer_mean:,.5g}" if n_valid else "N/A")
c3.metric("Latest real index", f"{result['Real_Index'].dropna().iloc[-1]:,.2f}" if result['Real_Index'].notna().any() else "N/A")
c4.metric("Latest nominal S", f"{result['S_currency2_per_currency1'].dropna().iloc[-1]:,.6g}" if result['S_currency2_per_currency1'].notna().any() else "N/A")

if n_valid:
    st.altair_chart(chart_for(result, mode, f"{country2}/{country1}"), use_container_width=True)
    st.caption("Blue: real index (left axis). Orange: nominal currency units (right axis in Both mode). "
               "Zoom or pan using the chart controls; hover for monthly values.")
else:
    st.warning("No overlapping months contain both CPIs and both USD exchange-rate observations. "
               "Try a different pair or period. The IMF's historical coverage differs by economy.")

if n_valid < len(result):
    st.info(f"{len(result) - n_valid:,} of {len(result):,} months lack at least one input. "
            "These months remain in the export with blank calculated values; no interpolation is used.")

st.download_button("⬇ Download selected sample as CSV", data=export_csv(result, rer_mean),
                   file_name=f"imf_rer_{country1}_{country2}_{start}_{end}.csv",
                   mime="text/csv", disabled=not n_valid)
with st.expander("View observations and calculations"):
    st.dataframe(result.reset_index().assign(Month=lambda d: d["Month"].astype(str)),
                 hide_index=True, use_container_width=True)
with st.expander("Definitions and methodology"):
    st.markdown(r"""
**Currency direction:** \(S_t\) = units of Country 2 currency per unit of Country 1 currency.

**IMF USD quotes:** \(E_{i,t}\) = local-currency units per USD, so
\(S_t=E_{2,t}/E_{1,t}\). For the United States \(E_{USA,t}=1\).

**Real bilateral exchange rate:** \(RER_t=S_t P_{1,t}/P_{2,t}\), where
\(P_{i,t}\) is that economy's monthly IMF CPI index.

**Sample benchmark:** \(RER^*\) is the arithmetic mean of valid \(RER_t\)
observations **within the selected start and end months**. The plotted
\(RealIndex_t=100RER_t/RER^*\). Changing dates recomputes the benchmark.

**Interpretation:** An increase means a real appreciation of Country 1 currency
relative to Country 2 under this definition. CPI index base years can differ
by economy, so raw RER levels are not cross-pair comparable; the 100-based
within-pair index is intended for time-series analysis. No missing data are filled.

**Data:** IMF SDMX 3.0, IMF.STA:CPI (`CPI._T.IX.M`) and IMF.STA:ER
(`XDC_USD.PA_RT.M`), monthly period-average nominal rates.
""")

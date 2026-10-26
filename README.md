# real-exchange-rate-app

A Python 3.12 Streamlit application to retrieve monthly IMF consumer-price-index and nominal exchange-rate observations, calculate bilateral real exchange rates, and explore or export the resulting time series.

## Run locally

1. Install **Python 3.12**.
2. Place `app.py`, `requirements.txt`, and `README.md` together in one folder.
3. From that folder, run:

   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate           # Windows PowerShell: .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   streamlit run app.py
   ```

4. Open the local URL Streamlit prints (normally `http://localhost:8501`). An internet connection is required to contact the IMF API. No API key is required.

## User guide

Choose Country 1 (base currency), Country 2 (quote currency), the first and last month, and **Real**, **Nominal**, or **Both**. The chart and sample statistics recompute automatically when inputs change. **Both** uses independent vertical axes. Hover over the lines for values. Use **Download selected sample as CSV** to export **every** selected month, including blank entries for unavailable observations, underlying CPI and USD rates, bilateral nominal exchange rate, RER, real index, sample benchmark, and a validity flag. The beginning of the historical query is January 1976, though data availability is different for every series.

Country choices use ISO alpha-3 country names from `pycountry` plus the Euro area (`G163` in the IMF). Being selectable does **not** guarantee that the IMF publishes all four input series for a selected country pair.

## Definitions

Let:

- `P1(t)` and `P2(t)` be monthly CPI values for Country 1 and Country 2.
- `E1(t)` and `E2(t)` be the IMF **local currency per USD** monthly period-average rates. When an area is `USA`, set its own `E(t)=1`.
- `S(t) = E2(t) / E1(t)` = **Country 2 currency per one Country 1 currency**.
- `RER(t) = S(t) * P1(t) / P2(t)`.
- `RER* = arithmetic mean of RER(t)` over selected months for which all four inputs are valid, positive and finite.
- `Real Index(t) = 100 * RER(t) / RER*`.

Changing the date range changes the set of observations and thus `RER*` and the real index. The nominal series remains **unindexed** in currency units. There is no interpolation, forward fill, or other imputation. Raw bilateral RER values are affected by CPI base-year conventions; the sample-indexed values are easier to compare over time within a given pair. A rise in the index corresponds to a real appreciation of Country 1 currency under these conventions.

## Data endpoints

The app uses the [IMF SDMX 3.0 API](https://api.imf.org/external/sdmx/3.0) via:

- `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/CPI/~/USA.CPI._T.IX.M`
- `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/ER/~/IND.XDC_USD.PA_RT.M`

The `c[TIME_PERIOD]=ge:1976-M01` constraint is added to requests, along with `Accept: text/csv`. Key formats are `<ISO3>.CPI._T.IX.M` and `<ISO3>.XDC_USD.PA_RT.M`; for the Euro area, substitute `G163`. The app uses the latest published dataflow revision (`~`). IMF series can be revised, unavailable, or subject to changes in API format or rate limits.

Results are cached for 12 hours to avoid unnecessary API requests. **Refresh cached IMF data** clears the cache. If the IMF API is unavailable or the request format changes, the app displays an error rather than fabricated estimates. Some countries have limited or no coverage; an empty selection may be a legitimate result.

## Files

- `app.py` — complete app, IMF CSV requests, validation, calculations, interactive charts, export.
- `requirements.txt` — Python 3.12 dependencies.
- `README.md` — installation, sources, formulas, limitations, provenance.

## Prompt and model provenance

**Date:** 2026-10-26  
**Model:** OpenAI GPT-6  
**Task prompt (saved verbatim in substance):**

> Build a complete Python 3.12 application, providing app.py, requirements.txt, and README.md. Let users select two IMF countries (plus the Euro area G163), monthly dates from January 1976 where available, and Real/Nominal/Both charts. Fetch CPI `{ISO3}.CPI._T.IX.M` and monthly period-average currency-per-USD FX `{ISO3}.XDC_USD.PA_RT.M` using the IMF SDMX 3.0 API. Define S as Country 2 currency per Country 1 currency; compute S = FX2/FX1, RER = S × CPI1/CPI2, RER* as the mean over valid months in the selected sample, and Real Index = 100 × RER/RER*. Recompute when dates change. Plot unnormalized nominal S, use separate y axes when displaying both, export all sample-period observations/calculations as CSV, and handle missing data without inventing observations. Provide executable, documented, self-contained files with Python 3.12 compatibility.

## Validation notes

Source code has been compiled and calculation/CSV generation smoke-tested without a network connection. **Live IMF API retrieval could not be end-to-end tested in the file-building environment**, because outbound DNS access was unavailable. Test with a live internet connection in your own environment. If IMF changes its datasets, dimensions or CSV schemas, update the query keys or parser accordingly.

# URBANOVA Trend Detective — NASA Space Apps Challenge 2026

**Challenge:** [Be An Earth System Trend Detective!](https://www.spaceappschallenge.org/2026/challenges/earth-system-trend-detective/)

**In one sentence:** pick a question and a place on Earth, and the Trend Detective finds NASA variables that could be
changing there. For each one it plots 45 years of monthly data and tells you, in plain words:
- **what** is changing;
- **where**;
- **how fast** (per decade, with a 95 % confidence interval);
- **whether the change is significant**, by two statistical tests.

Built for high-school and college students, with no account and no installation for the user.

## How it meets the challenge objectives

| Objective | How the Trend Detective does it |
|---|---|
| **1. Identify Earth-science variables that could show a trend** | A catalog of **12 NASA variables** (air temperature mean, day and night; rainfall; humidity; root-zone and surface soil wetness; evapotranspiration; sunshine at the ground; cloud cover; wind; surface pressure). Each has a **simple definition**, "why it might change", its unit and the model or instrument behind it. The student answers *what are you curious about?* and *what kind of place is it?*, and the detective suggests the most telling variables. With AI available, a free model picks from the catalog only, phrasing reasons as things to look for, never as results. Otherwise a rule table does it. |
| **2. Acquire the data for a region** | Click anywhere on the map. The app fetches **NASA POWER monthly data, January 1981 to the last complete year**, for that location. One POWER cell is about 0.5° × 0.625° (about 55 km), so every value is a regional value, and the app says so. No key is needed, and responses are cached. |
| **3. Generate and visualise a time series** | An interactive chart of the **monthly anomalies** (the seasonal cycle removed), the **yearly means**, and the **trend line with its 95 % confidence band**. Toggles let the student keep the seasonal cycle and change the period, and the series downloads as CSV. Several variables or places can be compared side by side. |
| **4. Rate of change and significance** | The **Mann-Kendall test** (with ties) and **Sen's slope** with its 95 % CI; a **least-squares fit with a t-test** and its 95 % CI; Kendall's τ, R² and the lag-1 autocorrelation, with a warning when neighbouring months are correlated. A **plain-language verdict** reads "Yes (significant)", "Probably (the tests disagree)" or "Not yet (not significant, a longer record may change that)", together with the rate per decade and the total change over the period. |
| **Audience and approach** | A three-question guided start, **six watchable demos** that fill in the questions step by step with captions, a "What does significant mean?" explainer, and a help assistant that answers questions about the page. |

## Demo examples (NASA POWER, 1981–2025, checked 13 Nov 2026)

| Demo | Variable | Result |
|---|---|---|
| **New Cairo, Egypt** (desert city) | Daytime high temperature | **+0.31 °C per decade** (95 % CI 0.17–0.45), **significant**: both tests p < 0.001, about +1.4 °C since 1981 |
| **Svalbard, Arctic Norway** | Air temperature | **+0.70 °C per decade** (0.56–0.84), **significant**: more than twice Cairo's rate (Arctic amplification) |
| **Delhi, India** | Sunshine reaching the ground | **−0.57 MJ/m²/day per decade** (−0.65 to −0.49), **significant dimming**, consistent with air pollution |
| **Central Amazon, Brazil** | Root-zone soil wetness | **−0.020 per decade** (−0.025 to −0.015), **significant drying** |
| **Sahel, Niger** | Rainfall | **The tests disagree:** t-test p = 0.004, Mann-Kendall p = 0.46. The rainy-season recovery since the 1980s droughts is real in the straight-line fit, but most months are dry, so the rank test sees many ties. This teaches why two tests are better than one. |
| **Sydney, Australia** | Rainfall | **Not significant yet:** a drying tendency of −0.095 mm/day per decade (−0.20 to +0.01), p = 0.08. The interval includes zero, so a longer record is needed. |

## Methods

1. **Data:** the NASA POWER monthly point API, all catalog parameters in one request. Missing months (POWER's −999,
   for example sunshine and clouds before 1984) are **skipped, never treated as 0**, and counted in the notes.
2. **Seasonal cycle:** each calendar month's mean over the record is subtracted (anomalies). The raw series can be
   shown for comparison.
3. **Least squares:** `scipy.stats.linregress` on decimal years, giving the slope, its standard error, a two-sided
   t-test and a 95 % CI from the t distribution.
4. **Mann-Kendall:** the S statistic, the variance with the tie correction, a continuity-corrected z and a two-sided
   p-value. Sen's slope is the median of all pairwise slopes, with its CI from the Mann-Kendall variance. When most
   values repeat (rainless months), Sen's slope is exactly 0, so the least-squares rate is reported instead, with a
   note.
5. **Verdict:** significant when **both** tests give p < 0.05, likely when one does, not significant when neither does.
   A minimum of 10 years is required.
6. **Checked against independent calculations** (automated tests): scipy's `linregress` and `kendalltau`, a
   brute-force Mann-Kendall S and the pairwise-slope median, synthetic series with known slopes, and pure noise
   (rarely called significant).

## Data and resources used

**Used directly:**
- **NASA POWER** (Langley Research Center), monthly API:
  - the MERRA-2 reanalysis (NASA GMAO): temperature, humidity, wind, pressure, soil wetness, evapotranspiration;
  - rainfall corrected with GPCP/GPM;
  - CERES and GEWEX SRB satellite products: sunshine and clouds.
- *Attribution:* "Data obtained from the NASA Langley Research Center POWER Project funded through the NASA Earth Science
  Directorate Applied Science Program."

**Challenge resources consulted for the method and next steps:**
- [Giovanni](https://giovanni.gsfc.nasa.gov/), the [GES DISC Hydrology Time Series](https://disc.gsfc.nasa.gov/information/tools?title=Hydrology%20Time%20Series)
  and [AppEEARS](https://appeears.earthdatacloud.nasa.gov/): the planned extension to GPM IMERG, GLDAS, MODIS and OMI
  variables, which need a free NASA Earthdata login;
- [NOAA Climate Data Online](https://www.ncei.noaa.gov/cdo-web/) and the
  [KNMI Climate Explorer](https://climexp.knmi.nl/): station cross-checks (URBANOVA already validates its climate
  baseline against GHCN stations, see `docs/VALIDATION.md`);
- [Time-series analysis of SeaWiFS chlorophyll](https://ntrs.nasa.gov/api/citations/20090042747/downloads/20090042747.pdf)
  and the trend-testing guides: the significance-testing approach.

**Part two, URBANOVA:** with a free account, "Zoom into a plot here" turns a regional trend into a **site analysis** of a
single plot. That covers satellite surface temperature, vegetation, land cover, and heat stress in sun and shade, and
with a design plan it adds design hotspots, options and costs. The story runs from *"the region is warming"* to *"what
this means for the street you are designing"*.

## Limitations (stated in the app)
- One value per cell of about 55 km: regional trends, not a single street or field.
- Linear trends only. Abrupt shifts and cycles longer than a year (El Niño) are not modelled.
- Neighbouring months are often correlated, which makes both tests somewhat over-confident; the app warns when the
  lag-1 autocorrelation is above 0.3.
- Reanalysis is a model constrained by observations, not a direct measurement at a site.

## Run it
```bash
python -m venv .venv
```
```bash
.venv/Scripts/pip install -r requirements.txt
```
```bash
.venv/Scripts/python -m urbanova.main
```
Then open http://127.0.0.1:8000/#/detective (the default page). No API key is needed. Tests: `python -m pytest`.

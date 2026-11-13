# URBANOVA Trend Detective — NASA Space Apps Challenge 2026

**Challenge:** [Be An Earth System Trend Detective!](https://www.spaceappschallenge.org/2026/challenges/earth-system-trend-detective/)

**In one sentence:** pick a question and a place on Earth, and the Trend Detective finds NASA variables that could be
changing there. For each one it plots 45 years of monthly data and tells you, in plain words:
- **what** is changing;
- **where**;
- **how fast** (per decade, with a 95 % confidence interval);
- **whether the change is significant**, by two statistical tests.

Built for high-school and college students, with no account and no installation for the user.

## Inside URBANOVA: every site analysis is a trend detective
URBANOVA is our urban-heat decision-support platform. A planner gives it a plot (coordinates, optionally a boundary and
a design plan), and it analyses the land as it is today:
- climate baseline;
- Landsat surface temperature, Sentinel-2 vegetation and ESA land cover;
- OpenStreetMap context;
- heat stress (UTCI) in sun and shade;
- then design hotspots and measured options, once a design is uploaded.

For this challenge we added module **A1b · Long-term climate trends** to that analysis. Every URBANOVA run now answers
the challenge's question for the plot: **is the climate here changing since 1981, how fast, and is it significant?**

| Challenge step | Where it happens in a URBANOVA run |
|---|---|
| 1. Select variables | Daytime high, mean and night-time air temperature; rainfall; humidity; sunshine at the ground. These are the variables that drive urban heat and comfort. |
| 2. Acquire data for the region | NASA POWER monthly, January 1981 to the last complete year, at the plot's coordinates, in one keyless request recorded as a dataset with provenance. |
| 3. Time series and visualisation | Each project's own **🔎 Trend Detective tab**: the plot inside the boundary (Landsat, change maps per pixel) and the region around it (NASA POWER), one row per variable with the yearly chart, trend line and 95 % band, plus the full detective tools fixed to the site. Results shows a summary card; the PDF report has the tables and charts. See [docs/TESTING_TRENDS.md](docs/TESTING_TRENDS.md) for test sites. |
| 4. Rate and significance | Sen's slope per decade with a 95 % range, the corrected Mann-Kendall test and the corrected t-test (both must agree). A significant warming becomes a **finding** ("The site's climate is warming: +0.33 °C per decade"). The **results chat** answers "Is it getting hotter here?" from the stored numbers. |
| Students | Each row links to the **Trend Detective** (`#/detective`), which gives the same numbers from the same code, plus seasons, the regional trend map, demos and data download. |

**Down to the plot itself: A1c · Site trends inside the boundary.** NASA POWER describes a ~55 km region. With the
project's site boundary, URBANOVA also reads the **Landsat archive (Landsat 5, 7, 8, 9; NASA/USGS; 30 m) from 1984**:
- **per year:** the clearest hot-season scenes, measured inside the boundary and in the ring around it;
- **three trends:** site surface temperature; **site minus surroundings** (is the plot heating faster than its
  neighbourhood? weather and sensor differences cancel); and site greenness (NDVI);
- **checks:** each trend is checked before and after the 2013 change of thermal sensor;
- **change maps:** every pixel is tested, with a false-discovery-rate correction, so the map shows *where* on the plot
  it changed;
- **findings:** "The site has been heating faster than its surroundings" or "Green cover on the site has declined",
  when significant.

**One code path:** the module and the Detective both call `urbanova/detective.py: analyse_variable`, so their numbers are
identical (tested). All the verification and validation below applies to both.

## How it meets the challenge objectives

| Objective | How the Trend Detective does it |
|---|---|
| **1. Identify Earth-science variables that could show a trend** | A catalog of **12 NASA variables** (air temperature mean, day and night; rainfall; humidity; root-zone and surface soil wetness; evapotranspiration; sunshine at the ground; cloud cover; wind; surface pressure). Each has a **simple definition**, "why it might change", its unit and the model or instrument behind it. The student answers *what are you curious about?* and *what kind of place is it?*, and the detective suggests the most telling variables. With AI available, a free model picks from the catalog only, phrasing reasons as things to look for, never as results. Otherwise a rule table does it. |
| **2. Acquire the data for a region** | Click anywhere on the map. The app fetches **NASA POWER monthly data, January 1981 to the last complete year**, for that location. One POWER cell is about 0.5° × 0.625° (about 55 km), so every value is a regional value, and the app says so. No key is needed, and responses are cached. |
| **3. Generate and visualise a time series** | A chart of the **monthly anomalies** (the seasonal cycle removed), the **yearly averages**, and the **trend line with its 95 % confidence band**, with a legend, scaled to the yearly story. A **"Which months are changing most?"** chart shows the trend of each calendar month. Students can change the period, show the seasonal cycle, compare up to 3 variables or places side by side, download CSV and **share a link** to any result. |
| **"Where" — the same variable trending in opposite directions** | **"Where is it changing?"** tests every NASA cell in a 5° × 5° window (about 80 cells, one request) and maps them: colour = direction and speed, solid = significant, faded = not. Example: around Perth the **coast is drying significantly while a strip inland is getting wetter**. The demo **"Same planet, opposite rain"** puts the wetting Sahel next to drying south-west Australia. |
| **4. Rate of change and significance** | On the **yearly averages** of the anomalies: the **Mann-Kendall test** (with ties) and **Sen's slope** with its 95 % CI, plus a **least-squares fit with a t-test** and its 95 % CI. A big headline number gives the **total change since 1981**, with a short verdict: "Yes", "Probably (the tests disagree)" or "Not yet (a longer record may change that)". Kendall's τ, R², the autocorrelation and the monthly-basis comparison sit in a "For scientists" section. |
| **Audience and approach** | A three-question guided start, **seven watchable demos** (each card shows its answer before you click), a "What does significant mean?" explainer, plain units (soil wetness in %), and a help assistant that answers questions about the page. |

## Demo examples (NASA POWER, 1981–2025, yearly averages, corrected tests, checked 13 Nov 2026)

| Demo | Variable | Result |
|---|---|---|
| **Same planet, opposite rain** | Rainfall | **Sahel +0.046 mm/day per decade** (95 % range +0.01 to +0.085) against **Perth −0.21** (−0.29 to −0.12): both significant, in opposite directions. Around Perth the coast dries while a strip inland gets wetter; the biggest decline is in **June**, the winter rains. |
| **New Cairo, Egypt** (desert city) | Daytime high temperature | **+0.33 °C per decade** (+0.15 to +0.55), **significant**: about **+1.5 °C since 1981**. All 80 surrounding cells warm. |
| **Svalbard, Arctic Norway** | Air temperature | **+0.79 °C per decade** (+0.52 to +1.06), **significant**: about **+3.5 °C since 1981**, more than twice Cairo's rate (Arctic amplification) |
| **Delhi, India** | Sunshine reaching the ground | **−0.61 MJ/m²/day per decade** (−0.68 to −0.53), **significant dimming**. Checked against the satellite-product join (same trend on both sides) and against published station studies |
| **Sahel, Niger** | Rainfall | **+0.046 mm/day per decade**, **significant**: partial recovery since the 1980s droughts (ERA5 shows a weaker change, see validation) |
| **Sydney, Australia** | Rainfall | **Not significant yet:** −0.14 mm/day per decade (−0.30 to +0.048), p = 0.14 and 0.20 |
| **Central Amazon, Brazil** | Root-zone soil wetness | **Not significant yet:** −2.1 % per decade (−4.2 to +0.04). Soils "remember" from year to year, so 45 years count as only about 19 independent years; the uncorrected tests would wrongly call it significant |

## Verification and validation (full report: [docs/VALIDATION_SPACEAPPS.md](docs/VALIDATION_SPACEAPPS.md))
- **Same answers as independent software:** on 200 random series (with ties), every statistic matches the open-source
  **pymannkendall** package and **SciPy** exactly (largest difference 0).
- **Few false alarms (Monte Carlo, 2,000 trend-free records):** simple tests are fooled by year-to-year memory (28 %
  false alarms at correlation 0.6). Our corrected verdict stays near 5 % (3.3 %, 4.6 %, 5.9 %).
- **Many tests on the map** are controlled by the false discovery rate (Benjamini–Hochberg).
- **Data checks on every series:** missing values, physically impossible values, completeness. **Satellite-product
  joins** in sunshine and cloud records are tested on both sides of the shift.
- **An independent dataset:** every analysis is repeated on **ERA5** (ECMWF) at the demo places and 20 land points
  on every continent.
- **Published science:** each demo compared with peer-reviewed or agency values over the same periods; all directions
  agree, and size differences are reported.

## Methods

1. **Data:** the NASA POWER monthly point API, all catalog parameters in one request. Missing months (POWER's −999,
   for example sunshine and clouds before 1984) are **skipped, never treated as 0**, and counted in the notes.
2. **Seasonal cycle and yearly averages:** each calendar month's mean over the record is subtracted (anomalies), and the
   anomalies are averaged for each year with at least 10 months of data. The tests run on these **yearly averages**,
   because neighbouring months are strongly correlated (up to 0.84 for soil) and many monthly values repeat (rainless
   months, rounded soil wetness), which makes monthly tests over-confident. The monthly-basis results are shown for
   comparison in the scientists' view.
3. **Least squares:** `scipy.stats.linregress` on the yearly averages, with the t-test using an **effective sample size for
   year-to-year persistence** (Santer et al. 2000, bias-corrected lag-1), giving the slope, its standard error, a two-sided
   t-test and a 95 % CI from the t distribution.
4. **Mann-Kendall:** the **modified test of Yue & Wang (2004)**, whose variance is inflated for persistence: the S statistic, the
   variance with the tie correction, a continuity-corrected z and a two-sided
   p-value. Sen's slope is the median of all pairwise slopes, with its CI from the Mann-Kendall variance. If most
   yearly values repeat, Sen's slope is exactly 0, so the least-squares rate is reported instead, with a note.
   **Seasons:** a least-squares trend for each calendar month across the years. **Map:** the same yearly test for every
   cell of the NASA POWER regional API in a 5° × 5° window.
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

# URBANOVA Trend Detective: NASA Space Apps 2026

**Challenge:** [Be An Earth System Trend Detective!](https://www.spaceappschallenge.org/2026/challenges/earth-system-trend-detective/)

**Try it online, no install:**
- **Full app** (website + backend + AI): **https://urbanova-arch.vercel.app/**
- **Trend Detective only** (no account needed): **https://56-228-129-51.sslip.io/#/detective**

Or **run everything on your own computer** with [RUN_LOCALLY.md](RUN_LOCALLY.md).

**URBANOVA** is an urban-heat decision-support platform: give it a plot of land, and it analyses climate, satellite
surface temperature, vegetation, land cover and heat stress, and checks a design. For Space Apps we built a **Trend
Detective** into it. It answers the challenge question, *is it changing, how fast, and is the change significant?*, at
three levels:

| Level | Where | Data | For whom |
|---|---|---|---|
| **Anywhere on Earth** | Public **Trend Detective** page (`#/detective`), no account needed | NASA POWER monthly (MERRA-2, CERES, GPCP), 1981 to now; 12 variables | Students: guided questions, demos, maps |
| **The region around a project** | Every URBANOVA analysis: the "Long-term climate trends" module | NASA POWER, one ~55 km cell | Planners: context for the design's lifetime |
| **The plot itself** | Each project's **🔎 Trend Detective** tab: the "Site trends inside the boundary" module | Landsat 5/7/8/9 (NASA/USGS), 30 m, 1984 to now | Planners: is *this* site heating up or losing green? |

Every trend has:
- a **rate per decade** with a **95 % range**;
- the **Mann-Kendall test** and a **least-squares t-test**, both corrected for year-to-year persistence;
- a plain-language verdict: *significant* only when both tests agree.

**→ Run the full project (website + backend + AI) locally: [RUN_LOCALLY.md](RUN_LOCALLY.md)** · **Project write-up: [SUBMISSION.md](SUBMISSION.md)** · **Technical write-up: [SPACEAPPS.md](SPACEAPPS.md)** · **Validation report: [docs/VALIDATION_SPACEAPPS.md](docs/VALIDATION_SPACEAPPS.md)** · **2-minute demo script: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)**

## Quick start: one command

You need **Python 3.11 or 3.12** ([download](https://www.python.org/downloads/); on Windows tick "Add python.exe to
PATH"). Download the code, either with **Code → Download ZIP** on GitHub (then unzip it) or with
`git clone https://github.com/9amole/Urbanova-nasa.git`. Open a terminal in the folder, then:

| Your system | Command |
|---|---|
| **Windows** (Command Prompt or PowerShell) | `run.cmd` |
| **macOS / Linux** | `bash run.sh` |

The first run installs everything into a private `.venv` folder (about 3–5 minutes, once). Then open
**http://127.0.0.1:8000/#/detective**. The Trend Detective page needs no account and no API key. Press **Ctrl+C** to
stop. Later starts take a few seconds.

| Also available | Windows | macOS / Linux |
|---|---|---|
| Run the automated tests (offline, about 6 minutes) | `run.cmd test` | `bash run.sh test` |
| Reinstall the packages | `run.cmd setup` | `bash run.sh setup` |
| Seed a sample project with live data | `run.cmd demo` | `bash run.sh demo` |

<details><summary>Docker, or a manual setup</summary>

**Docker** (no Python needed). Build the image:

```bash
docker build -t urbanova .
```

Run it, keeping its data in a named volume:

```bash
docker run --rm -p 8000:8000 -v urbanova-data:/data urbanova
```

**Manual setup:**
1. `python -m venv .venv`
2. `.venv/Scripts/pip install -r requirements.txt`
3. `.venv/Scripts/python -m urbanova.main`

On macOS/Linux, use `.venv/bin/` instead of `.venv/Scripts/`.

**Settings:**
- Optional settings are listed in `.env.example`, for example `URBANOVA_PORT` to change the port.
- The help assistant can use a free AI model if you set your own key as an environment variable. Everything else works
  without one.
</details>

## Try it

**1 · Public Trend Detective.** Open `#/detective` and click a demo:

| Demo | Result (1981–2025, corrected tests) |
|---|---|
| Same planet, opposite rain | Sahel **+0.046 mm/day per decade** against Perth **−0.21**, both significant |
| New Cairo, Egypt | Daytime highs **+0.33 °C per decade** (+0.15 to +0.55), significant |
| Svalbard, Arctic | Air temperature **+0.79 °C per decade** (+0.52 to +1.06), significant |
| Delhi, India | Sunshine at the ground **−0.61 MJ/m²/day per decade**, significant dimming |
| Sydney, Australia | Rainfall: **not significant yet** |

**2 · A project's own Trend Detective.**
1. Register an account in the app.
2. Create a project with one of the test sites below, and upload its boundary file on **1 · Site**.
3. Confirm the location.
4. Open the **🔎 Trend Detective** tab and click **Run the trend detective**. The first run reads about 40 years of
   Landsat, so it takes 3–4 minutes.

| Test site | Latitude, longitude | Boundary file | Expected |
|---|---|---|---|
| New Cairo, Egypt (desert plot, built in the 2000s) | 30.0200, 31.5000 | `samples/spaceapps_new_cairo_plot.geojson` | The plot became **~3.9 °C cooler than the surrounding desert** in a step around 2005 |
| Novo Progresso, Brazil (forest cleared in the 2000s) | −7.0300, −55.4000 | `samples/spaceapps_novo_progresso_plot.geojson` | Surface temperature **+3.47 °C per decade** and greenness declining, both significant |
| URBANOVA sample site (synthetic design) | 30.1200, 31.8500 | `samples/sample_site_boundary.geojson` + the other `samples/` files | The full platform flow with a design, plus the trends |

Step-by-step checks and every expected value are in **[docs/TESTING_TRENDS.md](docs/TESTING_TRENDS.md)**.

## How we know it's right
- **Same numbers as independent software.** Every statistic matches the open-source `pymannkendall` package and SciPy
  on 200 random series (largest difference 0).
- **Few false alarms.** In 2,000 trend-free simulated records, the corrected verdict says "significant" 3.3–5.9 % of
  the time. Simple tests reach 28 % when years are correlated.
- **Checked against a second dataset (ERA5).** Temperature trends agree in direction at every place checked. Rainfall
  trends often disagree, so the app warns on every rainfall result.
- **Compared with published studies** (Svalbard warming, Indian solar dimming, south-west Australian drying, Sahel rain
  recovery): all directions agree.
- **Data checks:** missing values stay missing, impossible values are removed and counted, and satellite-product and
  Landsat sensor changes are tested.
- **384 automated tests**, run offline.

Details: [docs/VALIDATION_SPACEAPPS.md](docs/VALIDATION_SPACEAPPS.md). To reproduce the checks, install
`pymannkendall==1.4.3`, then run `python scripts/validate_detective.py`.

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

They run offline: data providers are faked, about 3–4 minutes. The reference comparison with `pymannkendall` runs when
that package is installed.

## Repository layout

```
urbanova/
  trends.py               the trend engine (Sen's slope, corrected Mann-Kendall and t-test, Pettitt, Benjamini–Hochberg)
  detective.py            public Trend Detective API: catalogue, trends, regional trend maps, suggestions
  analysis/
    climate_trends.py     A1b · regional climate trends inside every URBANOVA run (NASA POWER)
    site_trends.py        A1c · site trends inside the project boundary (Landsat 1984–, change maps)
    ...                   the URBANOVA modules: climate, surface heat, vegetation, land cover, shade, heat stress, ...
  providers/              NASA POWER (monthly + regional), Open-Meteo/ERA5, Planetary Computer (Landsat, Sentinel-2), OSM
  engine/                 the JSON analysis engine (contract in schemas/)
web/                      the app: detective.js (Trend Detective), app.js (projects), styles.css; no build step
samples/                  test boundaries and synthetic design inputs
scripts/validate_detective.py   calibration, power, reference and ERA5 checks → validation/detective/
docs/                     VALIDATION_SPACEAPPS.md, TESTING_TRENDS.md, DEMO_SCRIPT.md, URBANOVA_PHASE1.md (platform reference), ...
```

## Data and credits
- **NASA POWER** (NASA Langley Research Center, POWER Project, funded through the NASA Earth Science Directorate Applied
  Science Program): monthly MERRA-2, CERES / GEWEX SRB and GPCP-corrected rainfall.
- **Landsat Collection 2** (U.S. Geological Survey and NASA), via the Microsoft Planetary Computer.
- **ERA5** (ECMWF / Copernicus) via Open-Meteo, for the independent cross-check (CC BY 4.0).
- **Also used by the URBANOVA platform:** Sentinel-2 (ESA / Copernicus), ESA WorldCover, OpenStreetMap contributors
  (ODbL).

## Limits (also stated in the app)
- NASA POWER describes about 55 km regions, not streets.
- Landsat measures the temperature of the ground and roofs, not the air, from one or two clear summer passes a year.
- A trend describes the past. It is **not a forecast**.

## The URBANOVA platform
The full platform documentation (analysis modules, data-source approvals and licences, the engine contract, the
project workflow) is in **[docs/URBANOVA_PHASE1.md](docs/URBANOVA_PHASE1.md)** and
[docs/INTEGRATION_CONTRACT.md](docs/INTEGRATION_CONTRACT.md).

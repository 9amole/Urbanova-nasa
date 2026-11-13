# Testing the project 🔎 Trend Detective: sites, files and expected results

Every project has a **🔎 Trend Detective** tab (after 7 · Results). It tests the **plot inside the site boundary**
(Landsat 5/7/8/9, 30 m, since 1984) and the **region around it** (NASA POWER, since 1981). Below are three test
projects. The expected results come from live runs on 13 Nov 2026. Later runs may move slightly, because each new year
adds data.

## How to run a test (same for every site)
1. **Projects → New project.** Give it a name and enter the **latitude and longitude** from the table.
2. **1 · Site:** upload the **boundary file** (or paste its GeoJSON), check it on the map, then **Confirm location**.
3. Open **🔎 Trend Detective** and click **Run the trend detective**.
   - The first run of a site reads about 40 years of Landsat: about 3–4 minutes.
   - Later runs reuse the saved scenes: about 1.5 minutes.
   - If it says "the archive was probably busy", run it again; scenes already read are kept.
4. Check the expected values (table below), then try the tools:
   - switch the **change maps** on and off;
   - open each **row** (its chart, the step-change and sensor notes);
   - click a variable under **Investigate another variable here**, then **Where is it changing?**;
   - **ask** "Is it getting hotter here?" and "Is the site losing green cover?".

## The three test sites

| | Site | Latitude, longitude | Boundary file (in `samples/`) | What it shows |
|---|---|---|---|---|
| **A** | **New Cairo, Egypt.** Desert plot, built and irrigated in the 2000s. | **30.0200, 31.5000** | `spaceapps_new_cairo_plot.geojson` (500 m square) | A plot that **cooled** against the desert around it |
| **B** | **URBANOVA sample site** (synthetic design data) | **30.1200, 31.8500** | `sample_site_boundary.geojson` | The **whole URBANOVA flow** with a design, plus the detective tab on the same project |
| **C** | **Novo Progresso, Pará, Brazil.** Amazon forest cleared in the 2000s. | **−7.0300, −55.4000** | `spaceapps_novo_progresso_plot.geojson` (500 m square) | A plot that **heated up and lost its green cover** |

### A · New Cairo: expected results
- **Coverage:** 42 of 42 years, 84 scenes; hot months June–August.
- **The plot:**
  - **Site minus surroundings:** a **step around 2005** from +0.3 K to −3.6 K (the plot became about 3.9 °C cooler than
    the desert). The steady-trend verdict is "not significant": the change was a step, and the row says so.
  - **Site surface temperature:** step around 2008, from about 45.9 °C to 42.4 °C.
  - **Greenness (NDVI):** step around 2010, from 0.07 to 0.15 (irrigated green).
  - **Change map:** the plot shows blue (cooling) inside a red (warming) desert ring.
- **The region:** daytime highs **+0.33 °C per decade (+0.15 to +0.55), significant**. That gives the finding "The site's
  climate is warming".

### B · URBANOVA sample site: expected results
- **Inputs:** use the other sample files as in the main README:
  - `sample_plan_baseline.geojson` (design);
  - `sample_alternative_additions.geojson` (alternative);
  - `sample_planning_conditions.pdf`;
  - `sample_rates_TEST_ONLY.csv`;
  - tick "This project uses synthetic or test data".
- **What to check:**
  - Run the full analysis on 6 · Run analysis: hotspots, options and compare all work as before.
  - The Trend Detective tab shows the same trend run results, and Results shows the compact **Trends** card linking to
    the tab.
- **What not to check:** the site is a synthetic design on desert land, so there are no fixed trend values to check here.
  This test checks that **everything works together**.

### C · Novo Progresso: expected results
- **Coverage:** 42 of 42 years, 82 scenes; hot months July–September (the dry season).
- **The plot:**
  - **Site surface temperature:** **+3.47 °C per decade (+2.56 to +4.35), significant**, with a step around 2005
    (27.1 → 34.7 °C), when the forest was cleared.
  - **Greenness (NDVI):** **−0.061 per decade (−0.082 to −0.029), significant**. That gives the finding "Green cover on
    the site has declined".
  - **Site minus surroundings:** not significant. The land around the plot was cleared too, so the plot did not heat
    *faster* than its neighbourhood.
- **The region:** daytime highs **+0.70 °C per decade, significant**, which gives the warming finding.

## Other places you can try
Any coordinates work: draw or upload a boundary on 1 · Site.
- **Gurugram, India (28.4950, 77.0890):** a 2000s city plot on former farmland. 34 of 42 years are clear. The plot became
  **significantly cooler than the dry farmland around it** in the pre-monsoon months (−0.58 K per decade, step around 2006).
- Very cloudy places, or places with a sparse 1980s–90s archive, may have fewer than 10 clear years. The tab then says
  so, and gives no verdict.

## Automated tests
`pytest tests/test_trends_tab.py tests/test_site_trends.py tests/test_climate_trends.py`. These run offline, on a fake
Landsat archive and fake NASA POWER data.

# URBANOVA Trend Detective — NASA Space Apps 2026

13 November 2026

## Project Summary

URBANOVA Trend Detective answers one question about any place on Earth: **is it changing, how fast, and is the change
real or just chance?** It is our entry for the NASA Space Apps 2026 challenge
[Be An Earth System Trend Detective](https://www.spaceappschallenge.org/2026/challenges/earth-system-trend-detective/).

It is built into **URBANOVA**, our urban-heat decision-support platform. A planner gives URBANOVA a plot of land, and
it analyses the climate, satellite surface temperature, vegetation, land cover and heat stress, and checks a design.
The Trend Detective adds the long view, at three levels:

| Level | Where in the app | NASA data | For whom |
| --- | --- | --- | --- |
| Anywhere on Earth | Public Trend Detective page, no account | NASA POWER monthly, 1981 to now, 12 variables | Students and curious people |
| The region around a project | Every URBANOVA analysis | NASA POWER, one ~55 km cell | Planners: the climate a design will live in |
| The plot itself | Each project's Trend Detective tab | Landsat 5/7/8/9 (NASA/USGS), 30 m, 1984 to now | Planners: is this site heating up or losing green? |

Every result gives a rate per decade with a 95 % range, two statistical tests and a plain-language verdict. It helps
four groups:

1. **Students**, who learn what a trend and "significant" mean on real NASA data.
2. **Urban planners and developers**, who see how their site and its region have changed before they design.
3. **Cities and governments**, who can spot plots that heat up or lose green cover.
4. **Researchers**, who get transparent, reproducible numbers with every method stated.

## How We Addressed This Challenge

### Introduction

Cities are heating up, and the people who plan them rarely see the long-term record of the land they build on. NASA
has watched the whole planet for decades, but turning that record into a clear answer still needs a scientist. We
wanted anyone, from a student to a city planner, to ask "is it changing here?" and get an honest, tested answer in a
minute.

### Importance

- **For learning:** students see the full path a climate scientist takes: choose a variable, get the data, plot the
  time series, measure the rate and test it.
- **For planning:** a building lasts 50 years or more. A design for a site that has warmed 1.5 °C since 1981 should be
  planned for that heat.
- **For honesty:** many apps show a trend line and call it a fact. We show when a change is *not* significant yet, and
  why.

### Methodology

1. **Choose the variables.** The student answers two questions (what are you curious about, and what kind of place is
   it?). The app then suggests the most telling of 12 NASA variables: air temperature (mean, day, night), rainfall,
   humidity, soil wetness (root zone and surface), evapotranspiration, sunshine at the ground, cloud cover, wind and
   pressure.
2. **Get the data for a region.** NASA POWER monthly data from January 1981, fetched live with no key. For a project's
   plot, URBANOVA also reads every clear summer Landsat image since 1984, inside the site boundary and in the ring
   around it.
3. **Plot the time series.** Monthly values with the seasonal cycle removed, yearly averages, and the trend line with
   its 95 % band. A seasons chart shows which months change most, and a map tests about 80 cells around the point.
4. **Measure the rate and test it.** Sen's slope per decade with a 95 % range. Two tests: the Mann-Kendall test (Yue &
   Wang 2004) and a least-squares t-test with an effective sample size (Santer et al. 2000). Both are corrected for
   year-to-year persistence, and the verdict is "significant" only when both give p < 0.05.
5. **Check the result.** Missing months stay missing, impossible values are removed, and joins between satellite
   products or Landsat sensors are tested on both sides. A Pettitt test finds sudden steps, and maps use a
   false-discovery-rate correction.

### Benefits of the project

1. A tested answer for any place on Earth, in plain words, in about a minute.
2. The trend of the plot itself (30 m), not only of a 55 km region.
3. Maps that show *where* on and around a plot the land changed.
4. Findings and an assistant inside URBANOVA that bring the trends into design decisions.
5. Every method, source and limit stated next to the result.

### Problems faced

1. **Simple tests give false alarms.** Climate data has year-to-year memory, and plain tests called 28 % of trend-free
   records "significant". We tested several corrections by simulation and kept the one that stays near 5 %.
2. **Rainfall trends disagree between datasets.** Compared with ERA5, rainfall agreed in direction only about half the
   time, so the app now warns on every rainfall result.
3. **Satellite records change instruments.** Sunshine joins two satellite products, and Landsat changed thermal sensors
   in 2013. We test the trend on both sides of each change.
4. **Building a plot changes it in one jump.** A trend test misses a step, so we added a step-change test that reports
   the year and the size of the jump.
5. **Free services have limits.** The ERA5 service has an hourly limit, and the Landsat archive can be slow. The app
   saves what it has read and says when to try again.

### Achievements

1. A public Trend Detective with 7 watchable demos, guided questions, regional trend maps, CSV download and shareable
   links.
2. Trends inside every URBANOVA analysis, and a Trend Detective tab for each project's site boundary.
3. A tested result for a real plot: New Cairo became about 3.9 °C cooler than the desert around it once it was built
   and irrigated, while a cleared forest plot in Brazil heated by 3.47 °C per decade.
4. A full validation (next section) and 384 automated tests.

## Verification and Validation

Our data mentor asked us to verify everything, so every number the detective shows was checked twice: is the maths
right, and is the data right?

**The maths.** On 200 random series, with tied values, every statistic matches the open-source pymannkendall package
and SciPy (largest difference 0). We also simulated 2,000 records of 45 years with no trend at all. A good test should
call about 5 % of them significant by chance:

| Year-to-year memory (correlation) | Simple tests: false alarms | Our verdict: false alarms |
| --- | --- | --- |
| None (0) | 4.2 % | 3.3 % |
| Moderate (0.3) | 10.5 % | 4.6 % |
| Strong (0.6) | 28.2 % | 5.9 % |

**The data.** We repeated the analysis on ERA5 (made by Europe's ECMWF, independently of NASA) at 15 places on every
continent:

- **Air temperature:** same direction at 100 % of places, 95 % ranges overlap at 93 %, rates correlated at 0.89.
- **Daytime highs:** same direction at 100 %; the datasets differ on how fast.
- **Rainfall:** same direction at only 53 % of places. That is why every rainfall result carries a caution.

**Published science.** Each demo agrees in direction with peer-reviewed or agency values over the same period:

| Demo | Ours | Published |
| --- | --- | --- |
| Svalbard air temperature, 1991–2018 | +1.08 °C per decade | +1.7 °C per decade at the airport station ([Nordli et al. 2020](https://polarresearch.net/index.php/polar/article/view/3614)) |
| Delhi sunlight at the ground, 1984–2004 | −7.4 W/m² per decade | about −9 W/m² per decade at Indian stations ([Padma Kumari et al. 2007, cited in Soni et al.](https://acp.copernicus.org/preprints/acp-2017-429/acp-2017-429.pdf)) |
| Perth April–October rain | −28 % | −16 % since the 1970s ([Government of Western Australia](https://wa.gov.au/service/natural-resources/water-resources/how-the-south-west-tracking-rainfall-and-streamflow)) |
| Sahel rainfall | wetter since 1981 | partial recovery after the 1980s droughts ([Dong & Sutton 2015](https://www.nature.com/articles/nclimate2664.pdf)) |

The full report, with every method and number, is [docs/VALIDATION_SPACEAPPS.md](docs/VALIDATION_SPACEAPPS.md).

## Project Demos

- **Code and setup:** this repository. It runs locally with two commands (see the [README](README.md)), and the Trend
  Detective needs no account or key.
- **Test sites with expected results:** [docs/TESTING_TRENDS.md](docs/TESTING_TRENDS.md)
- **Slides:** link to add.
- **Video:** link to add.

### Demos in the app

| Demo | Result (corrected tests) |
| --- | --- |
| New Cairo, Egypt: daytime highs, 1981–2025 | +0.33 °C per decade (+0.15 to +0.55), significant: about +1.5 °C since 1981 |
| Svalbard, Arctic: air temperature | +0.79 °C per decade (+0.52 to +1.06), significant: more than twice Cairo's rate |
| Same planet, opposite rain | Sahel +0.046 mm/day per decade against Perth −0.21, both significant |
| Delhi, India: sunshine at the ground | −0.61 MJ/m²/day per decade, significant dimming |
| Sydney, Australia: rainfall | Not significant yet (p = 0.14): the range includes zero |
| New Cairo plot, 500 m (Landsat) | About 3.9 °C cooler than the surrounding desert, in a step around 2005, once built and irrigated |
| Novo Progresso, Brazil, 500 m (Landsat) | +3.47 °C per decade after the forest was cleared, and greenness declining, both significant |

### The script in the video

1. **The problem (10 s).** "NASA measures hundreds of things about the Earth. Which of them are changing where you
   live, by how much, and is the change real or just noise? Our Trend Detective answers that in under a minute."
2. **A watched demo (30 s).** Click New Cairo. The detective picks heat, a city, one NASA cell of about 55 km, and the
   variables to test. "+1.5 °C since 1981: both corrected tests agree it is significant."
3. **Reading the chart, and where (20 s).** "The pale line is each month with the seasons removed; the dots are yearly
   averages; green is the trend and its 95 % range." Where is it changing? "All 80 NASA cells around Cairo are
   warming."
4. **Opposite trends, and "not yet" (25 s).** The Sahel gets wetter while south-west Australia dries. Sydney is "not
   significant yet": a longer record may change that.
5. **Down to the plot (25 s).** In a URBANOVA project, the Trend Detective tab tests the site boundary with 40 years of
   Landsat. "This New Cairo plot was desert until 2005. Then it was built and irrigated, and it became almost 4 °C
   cooler than the desert around it."
6. **Close (10 s).** "A question, a place, NASA data and two statistical tests. Anyone can be an Earth system trend
   detective."

## Resources

### NASA and space-agency data

1. [NASA POWER](https://power.larc.nasa.gov/): monthly point and regional data, built from the MERRA-2 reanalysis,
   CERES and GEWEX SRB satellite products, and GPCP-corrected rainfall. Used for every regional trend.
2. [Landsat Collection 2](https://www.usgs.gov/landsat-missions/landsat-collection-2) (USGS and NASA): Landsat 5, 7, 8
   and 9 surface temperature and reflectance, read through the
   [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2). Used for the site
   trends.
3. [Sentinel-2](https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a) (ESA / Copernicus) and
   [ESA WorldCover](https://esa-worldcover.org/): vegetation and land cover in URBANOVA's site analysis.
4. [ERA5](https://open-meteo.com/en/docs/historical-weather-api) (ECMWF / Copernicus) through Open-Meteo: the
   independent cross-check of our trends.

### External resources

1. [pymannkendall](https://github.com/mmhs013/pymannkendall) and [SciPy](https://scipy.org/): the independent reference
   implementations our statistics are checked against.
2. Yue & Wang (2004), the modified Mann-Kendall test; Santer et al. (2000), the effective sample size for the t-test;
   Wilks (2016), false discovery rates for climate maps.
3. [OpenStreetMap](https://www.openstreetmap.org/copyright): base map and site context.
4. The published studies listed under Verification and Validation.

## Future Features

1. **More NASA instruments through Earthdata:** GPM rainfall, MODIS land surface temperature and vegetation, and GLDAS
   soil moisture, so a student can compare instruments for the same variable.
2. **Ground stations:** NOAA GHCN weather stations near each demo, to check the gridded trends against thermometers on
   the ground.
3. **ECOSTRESS heat at different times of day,** because Landsat only sees the morning.
4. **Beyond straight lines:** trends that speed up or slow down, and cycles such as El Niño.
5. **Arabic and other languages** for students outside the English-speaking world.
6. **Classroom mode:** saved investigations a teacher can share and a class can compare.

## NASA Hackathon Journey

We came to the hackathon with URBANOVA, a platform that analyses one plot of land as it is today, and left with a
detective that tells how that land and its region have changed for 40 years. The project grew in five steps:

1. **A public Trend Detective.** NASA POWER data, guided questions, demos and two statistical tests, for students
   anywhere.
2. **Our mentor's challenge: verify everything.** We rebuilt the tests to correct for year-to-year memory, checked them
   against independent software, simulated 2,000 trend-free records, compared with ERA5 and with published studies, and
   added a caution where the data is weak (rainfall).
3. **Into URBANOVA.** Every URBANOVA analysis now reports the regional climate trends, with a finding when the climate
   is warming significantly.
4. **Down to the plot.** Forty years of Landsat inside the site boundary, with change maps, a sensor check and a
   step-change test.
5. **A detective for every project.** Each project has its own Trend Detective tab, with tested sites in Egypt and
   Brazil.

The biggest lesson: an honest "not significant yet" is worth more than a confident wrong answer. We believe a tool that
shows students and planners how their place is really changing, and how sure we can be, helps build cities ready for
the climate they will face.

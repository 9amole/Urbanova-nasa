# Trend Detective — verification and validation

**NASA Space Apps 2026, "Be An Earth System Trend Detective".** Checked 13 Nov 2026.

Following our data mentor's advice to *verify and validate everything*, we checked the Trend Detective twice:
- **Is the maths right?** Do the statistics match independent software, and do they say "significant" only as often as
  they should?
- **Is the data right?** Is every series complete and physically possible, are joins between satellite products
  hidden in it, and do the trends agree with an independent dataset and with published science?

Every number below comes from `scripts/validate_detective.py` (results in `validation/detective/*.json`) or from the
automated tests (`tests/test_trends.py`, `tests/test_detective.py`). Run `python scripts/validate_detective.py` to
reproduce them.

## 1. The method in one paragraph

1. For a place and a variable, NASA POWER returns monthly values from January 1981 to December 2025.
2. Each calendar month's normal value is subtracted (anomalies), and the anomalies are averaged per year (years with at
   least 10 months).
3. The trend is tested on these yearly averages with two tests, both corrected for year-to-year persistence:
   - the **modified Mann-Kendall test** (Yue & Wang 2004) with **Sen's slope**;
   - **least squares with a t-test using an effective sample size** (Santer et al. 2000).
   - The lag-1 autocorrelation is corrected for its small-sample bias (Kendall 1954).
4. The verdict is "significant" when **both** corrected tests give p < 0.05, "likely" when one does, and "not
   significant yet" when neither does.
5. The trend map tests every NASA POWER cell in a 5° × 5° window and controls the **false discovery rate** at 10 %
   (Benjamini & Hochberg 1995; Wilks 2016).

## 2. Is the maths right?

### 2.1 Same answers as independent software
- **Test:** 200 random series (12–60 years, with year-to-year persistence, rounded so that values tie).
- **Compared with:** **pymannkendall 1.4.3** (`original_test`, MIT licence) and **SciPy** (`linregress`).
- **Statistics compared:** Mann-Kendall S, Var(S) (tie-corrected), z, p, Kendall's τ, Sen's slope, and the least-squares
  slope and p-value.
- **Result: largest difference 0** for every statistic (`validation/detective/reference.json`).
- The same comparison runs in the test suite on every change: `test_statistics_match_the_pymannkendall_reference`.

### 2.2 Few false alarms: Monte-Carlo calibration
- **Simulation:** 2,000 records of 45 years with **no trend at all**, with independent years and with year-to-year
  correlation (AR(1) 0.3 and 0.6).
- **What a good test does:** calls about 5 % of them "significant" by chance. The Monte-Carlo uncertainty is ±0.5 %.

| Year-to-year correlation | Simple tests (both) | **Our verdict (both corrected)** | Corrected t-test | Corrected Mann-Kendall |
|---|---|---|---|---|
| 0 | 4.2 % | **3.3 %** | 3.9 % | 4.4 % |
| 0.3 | 10.5 % | **4.6 %** | 5.5 % | 5.1 % |
| 0.6 | 28.2 % | **5.9 %** | 6.1 % | 7.4 % |

**What this means:**
- **Simple tests are fooled by year-to-year memory.** At correlation 0.6 they call more than a quarter of trend-free
  records "significant". The corrected tests stay close to 5 %.
- **How the correction was chosen:** we first used the Hamed & Rao (1998) correction with all lags. It gave **9 %
  false alarms even on independent data**, so we compared variants by simulation and chose Yue & Wang with
  bias-corrected lag-1, which stays nearest to 5 %.
- **The demo results changed:** the corrected tests turned the Amazon root-zone soil "drying" from "significant" into
  "**not significant yet**" (p = 0.055 and 0.095). Soil wetness persists so strongly that 45 years count as only about
  19 independent years.
- **In the test suite:** `test_corrected_tests_keep_false_alarms_near_five_percent`.

### 2.3 Why "not significant yet": power
How often the corrected verdict detects a real trend (trend size in units of the year-to-year noise per decade; noise
AR(1) 0.2):

| Trend per decade | 15 years | 30 years | 45 years |
|---|---|---|---|
| 0.1 | 5.2 % | 6.6 % | 8.4 % |
| 0.2 | 5.4 % | 9.0 % | 23.8 % |
| 0.3 | 7.6 % | 21.6 % | 48.6 % |
| 0.5 | 9.2 % | 41.2 % | 89.2 % |
| 1.0 | 24.2 % | 93.6 % | 100 % |

A moderate trend is almost never detected in 15 years but usually is in 45. This is why the app says "not significant
**yet**" and not "no trend".

### 2.4 Many tests at once (the trend map)
- **The problem:** testing about 80 cells at 5 % would light up about 4 cells by chance.
- **The fix:** cells stay "significant" only if they pass the Benjamini–Hochberg false-discovery-rate step (q = 10 %).
  The map summary reports both counts.
- **Tested** with 80 cells of pure noise: at most 1 remains (`test_the_map_corrects_for_many_tests`).

### 2.5 Edge cases (automated tests)
- tied values (Sen's slope = 0, so the least-squares rate is used, with a note);
- gaps and missing months (never 0);
- years with fewer than 10 months left out;
- records shorter than 10 years refused;
- seasonal cycle shown or removed (the verdict is unchanged);
- per-month trends;
- change-point detection (Pettitt 1979).

## 3. Is the data right?

### 3.1 Integrity of every series
On every fetch:
- POWER's fill value (−999) becomes missing;
- values outside the **physically possible range** for the variable are removed and counted (for example air
  temperature −90 to 60 °C, rain 0–200 mm/day, humidity 0–100 %, soil wetness 0–1);
- completeness is reported (months expected, received, valid).

The checks appear in the scientists' view of each result (`test_impossible_values_are_removed_and_satellite_joins_are_checked`).

### 3.2 Joins between satellite products
- **The risk:** NASA POWER's sunshine and cloud records combine **GEWEX SRB** and **CERES SYN1deg / FLASHFlux**
  (POWER documentation). A change of product can create a fake trend.
- **The check:** for these variables the trend is also tested on each side of the biggest shift (Pettitt change point).
- **Delhi sunshine:** shift in 2003, but the trend is the same on both sides: **−0.61 MJ/m²/day per decade
  (1984–2002)** and **−0.66 (2003–2025)**, both significant. **The dimming is not an artefact of the join.**
- **Cloud cover** at Delhi jumps from about +5 % to −6 % around 1999–2001, consistent with the product change. Cloud
  trends therefore carry a caution in the app.

### 3.3 An independent dataset: ERA5
- **Method:** the same analysis repeated on **ERA5** (ECMWF, through the Open-Meteo archive). ERA5 is produced
  independently of NASA's MERRA-2.
- **Coverage:** air temperature, daytime highs and rainfall at the demo places and 20 land points on every continent.
- **Results:** see `validation/detective/era5.json` and the table below.

Result at **15 places** (2026-11-13). Open-Meteo's free hourly limit stopped the other places of the run;
`scripts/validate_detective.py era5` resumes from its cache and adds them.

| Variable | Places | Same direction | Same direction where either is significant | 95 % ranges overlap | Rate correlation |
|---|---|---|---|---|---|
| Air temperature | 15 | 100 % | 100 % (15 places) | 93 % | 0.89 |
| Daytime high temperature | 15 | 100 % | 100 % (15 places) | 80 % | 0.09 |
| Rainfall | 15 | 53 % | 50 % (8 places) | 80 % | 0.60 |

**What this means:**
- **Air temperature trends are robust.** Two independent datasets agree on the direction everywhere, their 95 % ranges
  almost always overlap, and their rates are strongly correlated.
- **Daytime highs: the direction is robust, the size is not.** The datasets disagree on how fast, most at Svalbard
  (NASA POWER +0.27, ERA5 +1.11 °C per decade).
- **Rainfall trends are not robust.** Even where one dataset finds a significant trend, the other agrees on its
  direction only about half the time. **The app therefore warns on every rainfall result** to check it with a second
  source before relying on it. The Sahel and Perth demos are examples: NASA POWER finds a significant change, and ERA5
  the same direction but weaker or not significant.

Demo places (rate per decade, NASA POWER against ERA5):

| Place | Variable | NASA POWER | ERA5 | Same direction |
|---|---|---|---|---|
| New Cairo | Air temperature | +0.319 (significant) | +0.564 (significant) | yes |
| New Cairo | Daytime high temperature | +0.333 (significant) | +0.457 (significant) | yes |
| New Cairo | Rainfall | +0.009 (not significant) | -0.001 (not significant) | **no** |
| Svalbard | Air temperature | +0.791 (significant) | +1.211 (significant) | yes |
| Svalbard | Daytime high temperature | +0.272 (significant) | +1.111 (significant) | yes |
| Svalbard | Rainfall | +0.003 (not significant) | +0.075 (significant) | yes |
| Sahel | Air temperature | +0.163 (significant) | +0.322 (significant) | yes |
| Sahel | Daytime high temperature | +0.092 (not significant) | +0.333 (significant) | yes |
| Sahel | Rainfall | +0.046 (significant) | +0.003 (not significant) | yes |
| Perth | Air temperature | +0.172 (significant) | +0.159 (significant) | yes |
| Perth | Daytime high temperature | +0.239 (significant) | +0.203 (significant) | yes |
| Perth | Rainfall | -0.205 (significant) | -0.066 (not significant) | yes |
| Sydney | Air temperature | +0.315 (significant) | +0.255 (significant) | yes |
| Sydney | Daytime high temperature | +0.428 (significant) | +0.329 (significant) | yes |
| Sydney | Rainfall | -0.143 (not significant) | +0.005 (not significant) | **no** |
| Amazon | Air temperature | +0.321 (significant) | +0.202 (significant) | yes |
| Amazon | Daytime high temperature | +0.556 (significant) | +0.194 (significant) | yes |
| Amazon | Rainfall | -0.404 (significant) | -0.420 (significant) | yes |


### 3.4 Published science (same periods as each study)

| Demo | Ours (NASA POWER) | Published | Agreement |
|---|---|---|---|
| Svalbard air temperature, 1991–2018 | +1.08 °C per decade (+0.74 to +1.59) | +1.7 °C per decade at the Svalbard Airport station ([Nordli et al. 2020, *Polar Research*](https://polarresearch.net/index.php/polar/article/view/3614)) | Same direction and exceptional size; **ours about a third lower** (the grid cell mixes land, sea and glacier; one station) |
| Delhi sunshine, 1984–2004 | −7.4 W/m² per decade | −8.6 (1981–2004) and about −9 W/m² per decade at Indian stations ([Padma Kumari et al. 2007, cited in ACP preprint acp-2017-429](https://acp.copernicus.org/preprints/acp-2017-429/acp-2017-429.pdf)) | **Agrees** |
| Delhi sunshine, 2001–2018 | −5.2 W/m² per decade | −2.9 ± 1.9 W/m² per decade, CERES all-India average ([IIT Delhi study, summary](https://www.saurenergy.com/solar-energy-news/aerosols-key-factor-in-declining-solar-irradiance-in-india-iit-delhi-researchers)) | Same direction; stronger in Delhi than the all-India average |
| Perth April–October rain | −28 % (1981–1995 against 2011–2025) | −16 % since the 1970s ([Government of Western Australia, citing the Bureau of Meteorology](https://wa.gov.au/service/natural-resources/water-resources/how-the-south-west-tracking-rainfall-and-streamflow)) | Same direction; ours larger (different periods and baseline) |
| Sahel rainfall | Getting wetter since 1981, significant | Partial recovery since the 1980s drought ([Dong & Sutton 2015, *Nature Climate Change*](https://www.nature.com/articles/nclimate2664.pdf)) | **Agrees** |

All directions agree; where the size differs, the table says so.

## 4. What we changed because of these checks
1. **Year-to-year correction:** the tests are now corrected for year-to-year persistence, which was over-confident
   before (§2.2).
2. **Calibrated correction:** we replaced our first correction (Hamed & Rao), which gave too many false alarms (§2.2).
3. **Amazon demo:** the soil drying became "not significant yet", and the demo now teaches why (§2.2).
4. **Trend map:** many-test correction added (§2.4).
5. **Data checks:** integrity checks added, and satellite joins tested; cloud trends carry a caution (§3).

## 5. Limitations that remain
- One NASA POWER value covers about 55 km, so trends are regional and can differ from a single station (Svalbard).
- Reanalyses disagree most on rainfall; where NASA POWER and ERA5 disagree, the trend is less certain (§3.3).
- Only linear trends are tested. Abrupt shifts and multi-year cycles (El Niño) are not modelled; the change-point is
  shown as information.
- The literature comparison uses the studies' own periods and areas; the agreement is about direction and order of
  size, not exact values.

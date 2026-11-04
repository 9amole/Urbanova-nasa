# URBANOVA Phase 1 — guide for supervisor testing

This guide walks you through the whole prototype in about **60–90 minutes**. You don't need to know the code. You'll use synthetic sample files, so no real project data is needed.

> **What this is:** a Phase 1 prototype for review. It is **not production-ready**. Results are decision support only: they are not statutory approvals or engineering sign-off. Some severity thresholds are marked **"PROVISIONAL — not approved"** on purpose: approving or replacing them is one of the decisions we need from you.

---

## Part A — Install it (one time, about 10 minutes)

You need a Windows PC with internet access.

1. **Install Python 3.11** from <https://www.python.org/downloads/release/python-3119/> ("Windows installer (64-bit)"). On the first installer screen, tick **"Add python.exe to PATH"**.
2. **Unzip** the `urbanova-phase1` folder you received, for example to `Downloads\urbanova-phase1`.
3. Open the folder in File Explorer. Click the address bar, type `powershell` and press Enter. A blue or black window opens in that folder.
4. Paste this command and press Enter. It installs everything the app needs and takes a few minutes:
   ```
   powershell -ExecutionPolicy Bypass -File .\run.ps1 setup
   ```
5. Start the app:
   ```
   powershell -ExecutionPolicy Bypass -File .\run.ps1
   ```
   Leave this window open while you test. Closing it stops the app.
6. Open **<http://127.0.0.1:8000>** in Chrome or Edge.

**Optional technical check:** `powershell -ExecutionPolicy Bypass -File .\run.ps1 test` runs the automated tests. It takes about 40 seconds and should end with `153 passed`.

**If something goes wrong:**

| Problem | What to do |
|---|---|
| `python is not recognized` | Reinstall Python and tick "Add python.exe to PATH". |
| The page doesn't open | Check that the PowerShell window from step 5 is still open and shows no error. |
| The map is grey or shows a warning image | Press **Ctrl+F5** to reload the page. |
| An analysis shows "data unavailable" | Check your internet connection. Climate, map and satellite data come from online sources. |

---

## Part B — The full journey (about 60 minutes)

All sample files are in the **`samples`** folder. Follow the steps in order. After each step, the **"What to check"** notes say what you should see. Please write down anything that is unclear, wrong or surprising.

### 1. Create an account and a project

1. On the sign-in page, choose **Create account**. Use any email address and a password: the account is stored only on your PC.
2. Under **New project**:
   * **Name:** `Supervisor test`
   * **Latitude** `30.12`, **Longitude** `31.85`. You can also click the map to set them.
   * **Site boundary:** choose `samples/sample_site_boundary.geojson`.
   * **What do you want to find out?** Tick all the questions.
3. Click **Create project**.

**What to check:** the project opens on **1 · Site**. A warning says the location is not confirmed. The map shows the marker and a red boundary of about 4 ha. "Missing information" lists what is still absent and what each gap prevents.

### 2. Try to run analysis before confirming (deliberate test)

Open **6 · Run analysis**.

**What to check:** the app blocks the run and asks you to confirm the site location first. It should never analyse an unconfirmed site.

### 3. Confirm the location

Go back to **1 · Site** and click **"The marker and boundary show the correct site — confirm location"**.

### 4. Upload the design and documents

On **2 · Inputs**, upload these three files one at a time. Choose the matching entry in **"What are you uploading?"** each time:

| File | Choose |
|---|---|
| `sample_plan_baseline.geojson` | Urban plan / design geometry |
| `sample_planning_conditions.pdf` | Planning document |
| `sample_rates_TEST_ONLY.csv` | Unit-rate table (CSV) |

**What to check:**
* 12 plan features appear on the map and in the table (blocks, streets, car park, plaza, gardens, a tree).
* Features the app is unsure about are marked **needs review**. Try changing one feature's type or height and click **Save**: the correction is recorded and the original is kept.
* Any building without a height is reported. The app never invents a height.
* The rates file uses the test currency **XTS** on purpose. These are not real prices.

Then click **Create baseline scenario from these features**. This takes a frozen snapshot of the original design.

### 5. Review the planning rules

Open **3 · Planning rules**.

**What to check:**
* Each condition from the PDF shows its **exact wording and page**. Click **Open source document** to check it against the PDF.
* The sample PDF deliberately gives **two different plot areas**. The app flags the conflict instead of choosing one. Verify one and reject the other.
* **Verify** the rules you agree with and **Reject** any you don't. Only verified rules are used in the checks.

### 6. Check what can be analysed

Open **4 · Readiness**.

**What to check:** every analysis shows **Ready**, **Ready with limitations** or **Blocked**, with the missing inputs and a next step. Population and air quality are listed as not available in this build. They are not faked.

### 7. Create an alternative design

Open **5 · Scenarios**.

1. Name the new alternative `More trees and shade` and click **Create alternative from baseline**.
2. In the alternative's feature table, find **"Surface car park"** and click **Remove**.
3. Under **Add features from GeoJSON**, choose `samples/sample_alternative_additions.geojson` and click **Add to this alternative**. This adds trees, a shade canopy and a garden.

**What to check:** the baseline stays read-only. Open **Change log** to see every change listed.

### 8. Run the analysis

Open **6 · Run analysis** and click **Run all active scenarios with these settings**. This takes about 1–2 minutes and needs internet. Progress is shown for each module.

### 9. Read the results

Open **7 · Results** and look at the baseline and the alternative.

**What to check:**
* **Status banner:** the report is expected to say **partial**, for two reasons: the 4 ha sample site is small compared with the 100 m resolution of the satellite heat data, and the sample point is in the desert, where OpenStreetMap has no buildings. Each limitation is stated.
* **Your questions:** each question you ticked shows *answered*, *partly answered* or *cannot be answered*.
* **Map layers:** design, OSM context, surface temperature (Landsat), relative heat classes, vegetation (NDVI) and shade.
* **Findings:** each finding shows its evidence. Click **Explain this finding**. Thresholds that are not approved are labelled **PROVISIONAL**.
* **Provenance:** for each number, the source, the date and whether it is *observed*, *calculated*, *modelled* and so on.
* Missing data is shown as **unknown or unavailable, never as 0**.
* Optional: mark a finding **Acceptable for project**, **Not acceptable** or **Flag as incorrect**.

### 10. Compare the designs

Open **8 · Compare**, tick the alternative and click **Compare**.

**What to check:**
* The green space, hard surface and shade differences, with their direction (improvement or regression).
* Planning checks for both designs.
* Indicative costs priced only from the test rates. Items without a rate show **"price required"** instead of a guessed price.

### 11. Produce the report

Open **9 · Report**. Select the comparison and click **Generate PDF + HTML report**. Then download the **PDF**, the **HTML** and the **Data export (ZIP)**.

**What to check:** the report lists every source, method, limitation, provisional threshold and data licence. It is labelled *partial* where anything was limited.

---

## Part C — Optional "try to break it" tests (about 10 minutes)

The app should give a clear message every time, never a crash or a silent wrong result.

* Create a project with latitude `200`.
* Upload a renamed file (for example, a `.txt` renamed to `.pdf`) as a planning document.
* On **1 · Site**, change the coordinates slightly and save. The location must be confirmed again, and earlier results are marked **out of date**.
* Try comparing after changing the alternative without re-running the analysis.

---

## Part D — Decisions we need from you

These are explained in `docs/DECISIONS.md` §5. The most important are:

1. **Data sources:** confirm Open-Meteo/ERA5, NASA POWER and OpenStreetMap, and the Microsoft Planetary Computer route for Landsat and Sentinel-2. The project owner approved that route on 2026-11-01, and your confirmation is requested.
2. **Provisional thresholds:** approve or replace them (`urbanova/config/rules.json`).
3. **Commercial use:** Open-Meteo's free tier is for non-commercial use only. For commercial use, choose a paid subscription or another source.
4. **Upload formats:** confirm GeoJSON, plus DXF with an EPSG code, for the MVP.
5. **Scope:** decide whether billing, notifications, organisations and admin (Modules 11–14) are part of Phase 1.
6. **Real inputs:** provide a real boundary, plan, planning document and quoted rates for validation.

## Feedback

For each step, please note:
* **Worked as expected** — yes or no.
* **Unclear** — what confused you.
* **Wrong or missing** — what you expected instead.

A screenshot helps a lot.

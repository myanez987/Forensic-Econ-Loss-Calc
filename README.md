[README.md](https://github.com/user-attachments/files/23280300/README.md)
# Forensic Economic Loss Calculator

This repository provides a proof‑of‑concept forensic economic loss calculator designed for wrongful‑death cases.  It contains Python modules for calculating life expectancy, work‑life expectancy, wage growth, earnings projections, discount factors and present values, along with a simple web application to run new cases.

## Quickstart

1. **Clone the repository or copy the files** to your local machine.

2. **Create and activate a virtual environment** (optional but recommended):

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install the required dependencies**.  The main requirements are `pandas`, `numpy`, `matplotlib`, and `streamlit`.  You can install them with:

   ```bash
   pip install pandas numpy matplotlib streamlit openpyxl
   ```

4. **Run the Streamlit web application**.  From the repository root, run:

   ```bash
   streamlit run app/app.py
   ```

   This will start a local web server and open the calculator in your browser.  Fill in the case details and click **Run Case**.  The app will generate an Excel workbook and a PDF memo in a new subdirectory under `./cases/<case_id>/` and present a summary table and chart on the page.  You can download the outputs via the buttons provided.

5. **Run via script**.  Alternatively, you can call the `run_case` function directly from Python.  For example:

   ```python
   from main import run_case

   cfg = {
       "case_id": "example_case",
       "person": {
           "first_name": "Jane",
           "last_name": "Doe",
           "sex": "female",
           "dob": "1980-01-01",
           "dod": "2025-01-01",
           "education_level": "BA",
           "active_status": "active",
       },
       "occupation": {
           "soc_code": "11-2022",
           "title": "Sales Manager",
           "county": "Riverside",
           "state": "CA",
           "base_salary_usd": 86900,
       },
       "assumptions": {
           "retirement_age_hint": 65,
           "life_expectancy_override_years": None,
           "worklife_table_override": None,
           "discount_rate_override": None,
           "annual_growth_rate_override": None,
       },
       "attachments": {},
   }
   result = run_case(cfg)
   print(result)
   ```

## Project Structure

```
.
├── main.py              # Core logic for economic loss calculations
├── app/
│   └── app.py           # Streamlit web application
├── cases/               # Outputs are saved here, one directory per case
├── slides/              # Directory for reference slide deck (optional)
├── README.md            # This file
└── requirements.txt     # (optional) list of dependencies for convenience
```

## Notes

* The current implementation uses 2023 CDC Life Tables and approximates work‑life expectancy due to limited access to the Skoog‑Ciecka‑Krueger tables.  Wage growth and discount rates are likewise based on accessible public sources.  These assumptions can be overridden via the input form.
* All outputs include an audit log with citations to the sources used.  Citations reference the relevant lines from the source documents.
* The generated Excel workbook includes several sheets: `dashboard`, `life_expectancy`, `worklife_lookup`, `wage_growth`, `projections`, `discount_factors`, `present_value`, and `audit_log`.  The dashboard sheet summarises the key inputs and the final total economic loss.

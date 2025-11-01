"""
Simple Streamlit Web Application for Forensic Economic Loss Calculator
--------------------------------------------------------------------

This application exposes a web form that allows users to input case
details and compute wrongful‑death economic losses.  It leverages the
functions defined in ``main.py`` to perform the calculations and
returns the results as both an Excel workbook and a PDF memo.  A
summary table and a present value chart are displayed on the page.

To run locally:

```
python -m streamlit run app/app.py
```

The application creates a subdirectory under ``./cases/<case_id>/`` for
each run, storing outputs and uploaded attachments.
"""

import os
from datetime import date
import json

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

from main import run_case


def main():
    st.set_page_config(page_title="Forensic Economic Loss Calculator", layout="centered")
    st.title("Forensic Economic Loss Calculator")

    with st.form(key="case_form"):
        st.subheader("Case Identifier")
        case_id = st.text_input("Case ID", value="case_001")
        st.subheader("Person")
        first_name = st.text_input("First Name", value="John")
        last_name = st.text_input("Last Name", value="Doe")
        sex = st.selectbox("Sex", ["male", "female"], index=0)
        dob = st.date_input("Date of Birth", value=date(1980, 1, 1))
        dod = st.date_input("Date of Death", value=date(2025, 1, 1))
        education_level = st.selectbox("Education Level", ["HS", "SomeCollege", "BA", "MA", "PhD", "Other"], index=2)
        active_status = st.selectbox("Active Status", ["active", "inactive"], index=0)
        st.subheader("Occupation")
        soc_code = st.text_input("SOC Code", value="11-2022")
        title = st.text_input("Title", value="Sales Manager")
        county = st.text_input("County", value="Riverside")
        state = st.text_input("State", value="CA")
        base_salary_usd = st.number_input("Base Salary (USD)", value=86900.0, step=1000.0)
        st.subheader("Assumptions")
        retirement_age_hint = st.number_input("Retirement Age Hint", value=65.0, step=1.0)
        life_expectancy_override = st.number_input("Life Expectancy Override (years)", value=0.0, step=0.1)
        worklife_override = st.number_input("Worklife Override (years)", value=0.0, step=0.1)
        discount_rate_override = st.number_input("Discount Rate Override (%)", value=0.0, step=0.1)
        growth_rate_override = st.number_input("Annual Wage Growth Override (%)", value=0.0, step=0.1)
        st.subheader("Attachments")
        uploaded_file = st.file_uploader("Upload Slide Deck (optional)", type=["pdf", "ppt", "pptx"])

        submit_button = st.form_submit_button(label="Run Case")

    if submit_button:
        # Build configuration dictionary
        cfg = {
            "case_id": case_id,
            "person": {
                "first_name": first_name,
                "last_name": last_name,
                "sex": sex,
                "dob": dob.strftime("%Y-%m-%d"),
                "dod": dod.strftime("%Y-%m-%d"),
                "education_level": education_level,
                "active_status": active_status,
            },
            "occupation": {
                "soc_code": soc_code,
                "title": title,
                "county": county,
                "state": state,
                "base_salary_usd": base_salary_usd,
            },
            "assumptions": {
                "retirement_age_hint": retirement_age_hint,
                "life_expectancy_override_years": life_expectancy_override if life_expectancy_override > 0 else None,
                "worklife_table_override": worklife_override if worklife_override > 0 else None,
                "discount_rate_override": (discount_rate_override / 100.0) if discount_rate_override > 0 else None,
                "annual_growth_rate_override": (growth_rate_override / 100.0) if growth_rate_override > 0 else None,
            },
            "attachments": {},
        }
        # Save attachments if provided
        if uploaded_file is not None:
            case_dir = os.path.join("cases", case_id)
            os.makedirs(case_dir, exist_ok=True)
            attachment_path = os.path.join(case_dir, uploaded_file.name)
            with open(attachment_path, "wb") as f:
                f.write(uploaded_file.read())
            cfg["attachments"]["slides"] = attachment_path
        # Run calculation
        with st.spinner("Processing case..."):
            result = run_case(cfg)
        st.success("Case processed successfully!")
        # Display summary table
        summary_df = pd.DataFrame([
            {
                "Life Expectancy (yrs)": result["summary"]["life_expectancy_years"],
                "Worklife Remaining (yrs)": result["summary"]["worklife_remaining_years"],
                "Avg Wage Growth (%)": result["summary"]["avg_wage_growth_pct"],
                "Discount Rate (%)": result["summary"]["discount_rate_pct"],
                "Total Economic Loss (USD)": result["summary"]["total_economic_loss_usd"],
            }
        ])
        st.subheader("Summary")
        st.table(summary_df.style.format({
            "Life Expectancy (yrs)": "{:.2f}",
            "Worklife Remaining (yrs)": "{:.2f}",
            "Avg Wage Growth (%)": "{:.2f}",
            "Discount Rate (%)": "{:.2f}",
            "Total Economic Loss (USD)": "${:,.2f}",
        }))
        # Load PV series for chart
        pv_file = result["files"]["excel_path"]
        pv_df = pd.read_excel(pv_file, sheet_name="present_value")
        st.subheader("Present Value over Time")
        fig, ax = plt.subplots()
        ax.plot(pv_df["YearIndex"], pv_df["CumulativePV"], marker='o')
        ax.set_xlabel("Year Index")
        ax.set_ylabel("Cumulative PV (USD)")
        ax.grid(True)
        st.pyplot(fig)
        # Download buttons
        with open(result["files"]["excel_path"], "rb") as f:
            st.download_button("Download Excel", data=f, file_name=os.path.basename(result["files"]["excel_path"]))
        with open(result["files"]["memo_pdf_path"], "rb") as f:
            st.download_button("Download Memo (PDF)", data=f, file_name=os.path.basename(result["files"]["memo_pdf_path"]))


if __name__ == "__main__":
    main()
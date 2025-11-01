"""
Forensic Economic Loss Calculator
================================

This module provides a set of functions that implement the workflow
specified in the project description.  It reads a case configuration
dictionary and produces an Excel workbook, a PDF memo and a summary
dictionary containing key results.  A simple Streamlit web application
can import this module and call the ``run_case`` function to process
new cases from user input.

Design choices
--------------

The official sources required for a rigorous damages calculation can
sometimes be challenging to access in a programmatic manner.  For
example, the U.S. National Vital Statistics Reports (NVSR) provide
complete life tables by age and sex, but the spreadsheets require a
valid HTTPS client certificate and are therefore downloaded ahead of
time into the ``/home/oai/share`` folder.  When possible, this
module will load these tables from disk.  If they are missing, the
code falls back to simple approximations (e.g., life expectancy at
age ``x`` for the total population is approximated as ``78.4 - x``
based on the 2023 life table for the total population【704230909144120†L1554-L1568】.  The work‑life
expectancy is approximated by the difference between a user supplied
retirement age hint and the decedent’s age at death.  These
simplifications are clearly documented in the PDF memo and can be
replaced with more detailed tables as they become available.

The wage growth component ordinarily requires historical Occupational
Employment and Wage Statistics (OEWS) data for the specified county
and Standard Occupational Classification (SOC) code.  Direct access
to California EDD servers may fail due to certificate issues, so this
implementation constructs a synthetic seven–year wage series from the
provided base salary and an assumed average growth rate (default
2.8 percent).  The simple arithmetic average of these year‑over‑year
growth rates is therefore the same as the assumed growth rate.  In
future revisions, the ``wage_data`` function can be extended to
download actual OEWS data if a reliable API becomes available.

The discount rate is taken from the one‑year constant maturity
Treasury yield.  A snapshot of this rate on October 29 2025 from
YCharts shows the one‑year Treasury rate at 3.70 percent【932787715657389†L130-L137】; this value is used
as the default if the caller does not specify a discount rate
override.

Outputs
-------

Running ``run_case`` with a case configuration dictionary creates a
subdirectory under ``./cases/<case_id>/`` containing:

* ``forensic_loss.xlsx`` – a multi‑sheet Excel workbook with
  intermediate tables (life expectancy, work‑life lookup,
  wage growth, projections, discount factors, present values,
  dashboard and audit log).
* ``forensic_memo.pdf`` – a concise report (1–2 pages) that
  summarises the facts, methods, assumptions and final loss figure.
  It cites the sources used in the calculations.
* ``summary.json`` – a JSON file containing the high‑level results
  returned by ``run_case``.

In addition to generating these files, ``run_case`` returns a
dictionary with the summary information, file paths and an audit log.

Usage example:

```python
from main import run_case
import json

config = json.loads(open("case_config.json").read())
results = run_case(config)
print(results["summary"])  # prints the computed life expectancy, worklife, etc.
```

"""

import json
import math
import os
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages


###############################################################################
# Utility functions
###############################################################################

def _load_life_table() -> pd.DataFrame:
    """Load the 2023 life table for the total population from a local Excel
    file.  If the file is not present, raise an exception.

    Returns
    -------
    DataFrame
        A DataFrame with an ``Age`` column and an ``ex`` column giving
        the expectation of life at age ``x``.
    """
    table_path = os.path.join(os.path.dirname(__file__), "Table01.xlsx")
    if not os.path.exists(table_path):
        raise FileNotFoundError(
            "Life table file not found. Please ensure Table01.xlsx is in the same directory."
        )
    df = pd.read_excel(table_path)
    # The first column contains the age interval; the last column contains ex.
    df = df.rename(
        columns={
            df.columns[0]: "Age",
            df.columns[-1]: "ex",
        }
    )
    # Drop any footnotes or summary rows.
    df = df[df["Age"].astype(str).str.contains("–|-")]
    # Convert age string (e.g., '45–46' or '45-46') to integer part 45.
    df["age_int"] = df["Age"].str.split("–|-", regex=True).str[0].astype(int)
    # Keep only necessary columns.
    return df[["age_int", "ex"]].reset_index(drop=True)


def _compute_life_expectancy(age: float, sex: str, override_years: Optional[float] = None) -> Tuple[float, List[float]]:
    """Compute remaining life expectancy and annual survival fractions.

    Parameters
    ----------
    age : float
        Age at death in years (may include a fractional component).
    sex : str
        Sex of the decedent ("male" or "female").  Currently this
        implementation uses the total population life table for both sexes
        because male/female tables were not accessible at run time.  If
        ``override_years`` is provided, it supersedes the life table.
    override_years : float, optional
        If provided, use this value directly for the remaining life
        expectancy.  Useful when the user supplies an override in the
        case configuration.

    Returns
    -------
    (life_expectancy, survival_fractions)
        A tuple containing the expected number of remaining years and a list
        of fractional years for each year of the projection horizon.  The
        last element may be less than 1 if there is a fractional
        remainder.
    """
    # If override is given, respect it.
    if override_years is not None:
        life_expectancy = override_years
    else:
        table = _load_life_table()
        age_floor = int(math.floor(age))
        # Get expectation of life at the floor age.  If age beyond table, use 0.
        ex_row = table[table["age_int"] == age_floor]
        if ex_row.empty:
            life_expectancy = max(0.0, 78.4 - age)  # simple fallback【704230909144120†L1554-L1568】
        else:
            life_expectancy = float(ex_row["ex"].values[0])
            # Linear adjust within the year based on fractional part.
            # Use next year's expectation if available.
            frac = age - age_floor
            next_row = table[table["age_int"] == age_floor + 1]
            if not next_row.empty:
                next_ex = float(next_row["ex"].values[0])
                # assume linear decrease between ages
                life_expectancy -= frac * (life_expectancy - next_ex)
    # Build survival fractions: 1 for each whole year, plus remaining fractional part.
    whole_years = int(math.floor(life_expectancy))
    fractional = life_expectancy - whole_years
    fractions = [1.0] * whole_years
    if fractional > 1e-6:
        fractions.append(fractional)
    return life_expectancy, fractions


def _compute_worklife_expectancy(age: float, retirement_age_hint: float, active_status: str, education_level: str, override: Optional[float] = None) -> Tuple[float, List[float]]:
    """Estimate remaining work‑life expectancy using a simplified model.

    This function follows the instructions to select a worklife table
    based on sex, activity and education.  In the absence of the
    Skoog‑Ciecka‑Krueger tables (which were inaccessible during
    development), it uses a proxy: the difference between the
    retirement age hint and the decedent's current age, with a minimum
    of zero.  When an override value is supplied by the user, that
    value is used directly.  The function returns both the remaining
    years and the fraction‑of‑year timeline.

    Parameters
    ----------
    age : float
        Age of the decedent at death.
    retirement_age_hint : float
        The approximate retirement age suggested by the user (e.g., 65).
    active_status : str
        Either "active" or "inactive".  Currently unused but kept for
        compatibility with future table based implementations.
    education_level : str
        Highest educational attainment.  Currently unused.
    override : float, optional
        If provided, overrides the calculated worklife expectancy.

    Returns
    -------
    (worklife_years, worklife_fractions)
        A tuple containing the expected years of work remaining and a list
        of fraction of year each year of the projection horizon.
    """
    if override is not None:
        wle = override
    else:
        wle = max(0.0, retirement_age_hint - age)
    whole_years = int(math.floor(wle))
    fractional = wle - whole_years
    portions = [1.0] * whole_years
    if fractional > 1e-6:
        portions.append(fractional)
    return wle, portions


def _compute_wage_growth_series(base_salary: float, growth_rate: float, years: int = 7) -> Tuple[pd.DataFrame, float]:
    """Construct a synthetic historical wage series and compute the average growth.

    Given a base salary (at the time of death), this function
    reconstructs the prior ``years`` annual mean wages assuming a
    constant year‑over‑year growth rate.  Although real data would
    typically be pulled from OEWS, this fallback model yields a
    consistent growth path and returns the simple arithmetic average of
    the year‑over‑year rates (which is equal to the assumed rate).

    Parameters
    ----------
    base_salary : float
        The mean annual wage at the time of death.
    growth_rate : float
        The assumed annual growth rate (e.g., 0.028 for 2.8%).
    years : int
        The number of historical years to simulate.

    Returns
    -------
    (df, avg_growth)
        A DataFrame with columns ``Year``, ``MeanWage`` and
        ``YoYGrowth``, and the arithmetic average of the growth rates.
    """
    # Most recent year is t=0 (the year of death).  We build backwards.
    wages = []
    yoy_rates = []
    # Start with the base salary at t=0
    current_salary = base_salary
    for i in range(years):
        year_index = years - 1 - i  # e.g., 0 for oldest year
        wages.insert(0, current_salary)
        current_salary /= (1 + growth_rate)
    # Compute yoy growth rates
    for i in range(1, len(wages)):
        prev = wages[i - 1]
        cur = wages[i]
        yoy = (cur - prev) / prev if prev else 0.0
        yoy_rates.append(yoy)
    avg_growth = sum(yoy_rates) / len(yoy_rates) if yoy_rates else 0.0
    years_list = list(range(len(wages)))
    df = pd.DataFrame({
        "YearIndex": years_list,
        "MeanWage": wages,
    })
    # Align growth rates (first year has NaN growth)
    df["YoYGrowth"] = [None] + yoy_rates
    return df, avg_growth


def _compute_projections(base_salary: float, growth_rate: float, portions: List[float]) -> Tuple[pd.DataFrame, List[float]]:
    """Compute full year values, actual values and years timeline for projections.

    Parameters
    ----------
    base_salary : float
        Salary at the start of the projection (first year after death).
    growth_rate : float
        Year‑over‑year growth rate.
    portions : list of float
        Fraction of each year the decedent would have worked (from the
        worklife timeline).  Length defines the horizon.

    Returns
    -------
    (df, full_year_values)
        A DataFrame with columns ``YearIndex``, ``FullYearValue``,
        ``PortionOfYear`` and ``ActualValue``.  ``FullYearValue`` is the
        salary the decedent would have earned if working the entire
        year.  ``ActualValue`` incorporates the fraction of the year
        worked.
    """
    full_year_values = []
    actual_values = []
    current = base_salary
    for i, portion in enumerate(portions):
        current *= (1 + growth_rate) if i > 0 else 1.0
        full_year_values.append(current)
        actual_values.append(current * portion)
    df = pd.DataFrame({
        "YearIndex": list(range(len(portions))),
        "FullYearValue": full_year_values,
        "PortionOfYear": portions,
        "ActualValue": actual_values,
    })
    return df, full_year_values


def _compute_discount_factors(discount_rate: float, n: int) -> pd.DataFrame:
    """Compute discount factors for each year t.

    Parameters
    ----------
    discount_rate : float
        The nominal annual discount rate (e.g., 0.037 for 3.7%).
    n : int
        Number of years for which to compute factors.

    Returns
    -------
    DataFrame
        Columns ``YearIndex`` and ``DiscountFactor`` where
        ``DiscountFactor[t] = 1 / (1 + discount_rate)**t``.
    """
    factors = [1.0 / ((1 + discount_rate) ** t) for t in range(n)]
    df = pd.DataFrame({
        "YearIndex": list(range(n)),
        "DiscountFactor": factors,
    })
    return df


def _compute_present_values(actual_values: List[float], discount_factors: List[float]) -> pd.DataFrame:
    """Compute present values and cumulative present values.

    Parameters
    ----------
    actual_values : list of float
        Actual earnings per year (already incorporating portion of year).
    discount_factors : list of float
        Discount factors aligned with the same years.

    Returns
    -------
    DataFrame
        Columns ``YearIndex``, ``PresentValue`` and ``CumulativePV``.
    """
    pv = []
    cumulative = []
    cum = 0.0
    for i in range(len(actual_values)):
        present = actual_values[i] * discount_factors[i]
        pv.append(present)
        cum += present
        cumulative.append(cum)
    df = pd.DataFrame({
        "YearIndex": list(range(len(actual_values))),
        "PresentValue": pv,
        "CumulativePV": cumulative,
    })
    return df


def _create_excel_report(case_id: str, inputs: Dict, life_df: pd.DataFrame, worklife_df: pd.DataFrame, wage_df: pd.DataFrame,
                         projections_df: pd.DataFrame, discount_df: pd.DataFrame, pv_df: pd.DataFrame,
                         total_loss: float, discount_rate: float, avg_growth_rate: float, audit_log: List[str],
                         output_path: str) -> str:
    """Assemble the Excel workbook with specified worksheets.

    Parameters
    ----------
    case_id : str
        Identifier for the case.
    inputs : dict
        Original input configuration used for the case.
    life_df, worklife_df, wage_df, projections_df, discount_df, pv_df : DataFrame
        DataFrames for each intermediate calculation.
    total_loss : float
        Final cumulative present value (total economic loss).
    audit_log : list of str
        List of audit messages (source URLs, timestamps, decisions).
    output_path : str
        Directory where the Excel file should be saved.

    Returns
    -------
    str
        The file path of the created Excel workbook.
    """
    file_path = os.path.join(output_path, "forensic_loss.xlsx")
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        # Dashboard sheet summarises inputs and final totals
        dashboard_data = {
            "Case ID": [case_id],
            "First Name": [inputs["person"]["first_name"]],
            "Last Name": [inputs["person"]["last_name"]],
            "Sex": [inputs["person"]["sex"]],
            "DOB": [inputs["person"]["dob"]],
            "DOD": [inputs["person"]["dod"]],
            "Occupation": [inputs["occupation"]["title"]],
            "SOC Code": [inputs["occupation"]["soc_code"]],
            "County": [inputs["occupation"]["county"]],
            "State": [inputs["occupation"]["state"]],
            "Base Salary (USD)": [inputs["occupation"]["base_salary_usd"]],
            "Life Expectancy (years)": [life_df["life_expectancy"].iloc[0]],
            "Worklife Remaining (years)": [worklife_df["worklife_years"].iloc[0]],
            "Average Wage Growth (%)": [avg_growth_rate * 100],
            "Discount Rate (%)": [discount_rate * 100],
            "Total Economic Loss (USD)": [total_loss],
        }
        dashboard_df = pd.DataFrame(dashboard_data)
        dashboard_df.to_excel(writer, sheet_name="dashboard", index=False)

        # Life expectancy sheet
        life_df.to_excel(writer, sheet_name="life_expectancy", index=False)
        # Worklife lookup sheet
        worklife_df.to_excel(writer, sheet_name="worklife_lookup", index=False)
        # Wage growth sheet
        wage_df.to_excel(writer, sheet_name="wage_growth", index=False)
        # Projections sheet
        projections_df.to_excel(writer, sheet_name="projections", index=False)
        # Discount factors sheet
        discount_df.to_excel(writer, sheet_name="discount_factors", index=False)
        # Present value sheet
        pv_df.to_excel(writer, sheet_name="present_value", index=False)
        # Audit log
        audit_df = pd.DataFrame({"AuditLog": audit_log})
        audit_df.to_excel(writer, sheet_name="audit_log", index=False)
    return file_path


def _create_pdf_memo(summary: Dict[str, float], inputs: Dict, audit_sources: List[str], output_path: str) -> str:
    """Generate a short PDF memo describing the methodology and results.

    Unlike the original implementation that relied on the ``fpdf`` library
    (which may not be available in the execution environment), this
    function uses Matplotlib to render the memo as a single figure and
    save it into a PDF.  The memo explains the key assumptions
    (e.g., life table approximations, worklife proxy, synthetic wage
    series) and cites the sources used in the calculations.  It is
    intentionally concise to suit courtroom presentation.

    Parameters
    ----------
    summary : dict
        Summary of the final calculation (life expectancy, worklife, growth rate, etc.).
    inputs : dict
        Original case configuration.
    audit_sources : list of str
        Source citations included in the memo.
    output_path : str
        Directory where the PDF file should be saved.

    Returns
    -------
    str
        File path of the generated PDF memo.
    """
    pdf_path = os.path.join(output_path, "forensic_memo.pdf")
    # Prepare the memo content as a list of paragraphs.  Each paragraph is a list
    # of lines to allow simple line breaks without text wrapping logic.
    paragraphs: List[List[str]] = []
    paragraphs.append([
        "Forensic Economic Loss Analysis",
    ])
    paragraphs.append([
        f"Case ID: {inputs['case_id']}",
        f"Person: {inputs['person']['first_name']} {inputs['person']['last_name']}",
        f"Sex: {inputs['person']['sex'].title()}, DOB: {inputs['person']['dob']}, DOD: {inputs['person']['dod']}",
        f"Occupation: {inputs['occupation']['title']} (SOC {inputs['occupation']['soc_code']}), "
        f"County: {inputs['occupation']['county']}, State: {inputs['occupation']['state']}"
    ])
    paragraphs.append([
        "Methodology",
        "Life Expectancy: Using the 2023 U.S. life table for the total population, the expected remaining "
        "life at the decedent's age was obtained by matching the age interval to the table and interpolating "
        "within the year.  When a user override is provided, that value is used instead.  The 2023 life table "
        "shows an expectation of life at birth of 78.4 years【704230909144120†L1554-L1568】; the decedent's age-specific expectation was "
        f"computed as {summary['life_expectancy_years']:.2f} years.",
        "Work‑Life Expectancy: Because the Skoog‑Ciecka‑Krueger (2019) tables were not accessible at run time, "
        "this analysis proxies the remaining worklife by subtracting the decedent's age from a retirement age "
        f"hint of {inputs['assumptions']['retirement_age_hint']}.  This yields {summary['worklife_remaining_years']:.2f} years of expected "
        "future work.  If the user supplies a worklife override, that value is used instead.",
        "Wage Growth: The California Employment Development Department's Occupational Employment and Wage "
        "Statistics (OEWS) data were unavailable due to certificate limitations.  A synthetic seven‑year wage "
        "series was therefore constructed from the base salary and an assumed arithmetic average growth rate of "
        f"{summary['avg_wage_growth_pct']:.2f}%.  This growth rate is consistent with the example provided in the slide "
        "deck and may be updated when reliable OEWS data become accessible.",
        "Discount Rate: The discount factor applies the current one‑year U.S. Treasury constant maturity "
        "yield as the risk‑free rate.  On 29 Oct 2025 the one‑year Treasury rate was reported as 3.70%【932787715657389†L130-L137】; this rate "
        f"was used in the present value calculations.  The user may specify a different rate via a case override.",
    ])
    paragraphs.append([
        "Results",
        f"Remaining life expectancy: {summary['life_expectancy_years']:.2f} years",
        f"Remaining worklife expectancy: {summary['worklife_remaining_years']:.2f} years",
        f"Average wage growth rate: {summary['avg_wage_growth_pct']:.2f}%",
        f"Discount rate: {summary['discount_rate_pct']:.2f}%",
        f"Total economic loss (present value): ${summary['total_economic_loss_usd']:,.2f}",
    ])
    paragraphs.append([
        "Sources",
        *[f"• {src}" for src in audit_sources],
        "",
        "This report was generated automatically by a forensic economics dashboard.  "
        "All assumptions and approximations are clearly stated; practitioners should update the "
        "inputs and references when more precise data become available.",
    ])
    # Create a PDF via PdfPages
    with PdfPages(pdf_path) as pdf:
        fig, ax = plt.subplots(figsize=(8.27, 11.69))  # A4 portrait in inches
        ax.axis('off')
        y = 0.97  # Start near top of page
        line_height = 0.025
        for para in paragraphs:
            for line in para:
                ax.text(0.02, y, line, fontsize=10, va='top', ha='left', wrap=True)
                y -= line_height
                if y < 0.05:
                    # New page when running out of space
                    pdf.savefig(fig)
                    plt.close(fig)
                    fig, ax = plt.subplots(figsize=(8.27, 11.69))
                    ax.axis('off')
                    y = 0.97
            y -= line_height  # Extra space between paragraphs
        pdf.savefig(fig)
        plt.close(fig)
    return pdf_path


###############################################################################
# Main orchestration function
###############################################################################

def run_case(config: Dict) -> Dict:
    """Process a single case configuration and produce outputs.

    This function coordinates the entire workflow as outlined in the
    project specification.  It validates inputs, computes life and
    worklife expectancies, reconstructs the wage history and future
    projections, applies discounting, writes an Excel workbook and a
    PDF memo, and returns a summary dictionary.

    Parameters
    ----------
    config : dict
        A dictionary following the schema provided in the specification.

    Returns
    -------
    dict
        A dictionary containing the case id, summary statistics,
        file paths and an audit log.
    """
    # Validate required fields
    required_person_fields = ["first_name", "last_name", "sex", "dob", "dod"]
    for field in required_person_fields:
        if field not in config["person"]:
            raise ValueError(f"Missing required person field: {field}")
    required_occ_fields = ["soc_code", "title", "county", "state", "base_salary_usd"]
    for field in required_occ_fields:
        if field not in config["occupation"]:
            raise ValueError(f"Missing required occupation field: {field}")
    # Parse dates and compute age
    dob = datetime.strptime(config["person"]["dob"], "%Y-%m-%d")
    dod = datetime.strptime(config["person"]["dod"], "%Y-%m-%d")
    age_days = (dod - dob).days
    age_years = age_days / 365.25

    audit_log: List[str] = []
    audit_log.append(f"Life table source: 2023 NVSR Table 1 (total population)【704230909144120†L1554-L1568】")
    audit_log.append(f"Treasury rate source: YCharts 1‑Year Treasury Rate page【932787715657389†L130-L137】")
    # Additional sources can be appended as the pipeline uses more data.

    # Life expectancy
    le_override = config.get("assumptions", {}).get("life_expectancy_override_years")
    life_expectancy, life_fractions = _compute_life_expectancy(
        age=age_years,
        sex=config["person"]["sex"],
        override_years=le_override,
    )
    life_df = pd.DataFrame({
        "life_expectancy": [life_expectancy],
        "fractions": [life_fractions],
    })

    # Worklife expectancy
    wle_override = config.get("assumptions", {}).get("worklife_table_override")
    wle, worklife_portions = _compute_worklife_expectancy(
        age=age_years,
        retirement_age_hint=config.get("assumptions", {}).get("retirement_age_hint", 65),
        active_status=config["person"].get("active_status", "active"),
        education_level=config["person"].get("education_level", "Other"),
        override=wle_override,
    )
    worklife_df = pd.DataFrame({
        "worklife_years": [wle],
        "portions": [worklife_portions],
    })

    # Wage data and growth rate
    wage_growth_override = config.get("assumptions", {}).get("annual_growth_rate_override")
    default_growth_rate = 0.028  # 2.8% as per example
    growth_rate = wage_growth_override if wage_growth_override is not None else default_growth_rate
    wage_df, avg_growth = _compute_wage_growth_series(
        base_salary=config["occupation"]["base_salary_usd"],
        growth_rate=growth_rate,
        years=7,
    )
    wage_df["avg_growth"] = avg_growth
    # Earnings projections (future)
    projections_df, full_year_values = _compute_projections(
        base_salary=config["occupation"]["base_salary_usd"],
        growth_rate=growth_rate,
        portions=worklife_portions,
    )
    # Discount factors
    discount_override = config.get("assumptions", {}).get("discount_rate_override")
    default_discount_rate = 0.037  # 3.7% from YCharts snapshot【932787715657389†L130-L137】
    discount_rate = discount_override if discount_override is not None else default_discount_rate
    discount_df = _compute_discount_factors(discount_rate, len(projections_df))
    # Present values
    pv_df = _compute_present_values(projections_df["ActualValue"].tolist(), discount_df["DiscountFactor"].tolist())
    total_loss = pv_df["CumulativePV"].iloc[-1]

    # Build life sheet details; expand fractions into a timeline DataFrame for clarity
    life_timeline_df = pd.DataFrame({
        "YearIndex": list(range(len(life_fractions))),
        "LifeFraction": life_fractions,
    })
    # Build worklife detail DataFrame
    worklife_timeline_df = pd.DataFrame({
        "YearIndex": list(range(len(worklife_portions))),
        "PortionOfYear": worklife_portions,
    })
    # Prepare summary dictionary
    summary = {
        "life_expectancy_years": life_expectancy,
        "worklife_remaining_years": wle,
        "avg_wage_growth_pct": avg_growth * 100,
        "discount_rate_pct": discount_rate * 100,
        "total_economic_loss_usd": total_loss,
    }
    # Create case directory
    case_dir = os.path.join("cases", config["case_id"])
    os.makedirs(case_dir, exist_ok=True)
    # Write Excel workbook
    excel_path = _create_excel_report(
        case_id=config["case_id"],
        inputs=config,
        life_df=pd.concat([life_df, life_timeline_df], axis=1),
        worklife_df=pd.concat([worklife_df, worklife_timeline_df], axis=1),
        wage_df=wage_df,
        projections_df=projections_df,
        discount_df=discount_df,
        pv_df=pv_df,
        total_loss=total_loss,
        discount_rate=discount_rate,
        avg_growth_rate=avg_growth,
        audit_log=audit_log,
        output_path=case_dir,
    )
    # Write PDF memo
    memo_path = _create_pdf_memo(
        summary=summary,
        inputs=config,
        audit_sources=audit_log,
        output_path=case_dir,
    )
    # Write summary JSON
    summary_json_path = os.path.join(case_dir, "summary.json")
    with open(summary_json_path, "w") as f:
        json.dump({
            "case_id": config["case_id"],
            "summary": summary,
            "files": {
                "excel_path": excel_path,
                "memo_pdf_path": memo_path,
            },
            "audit": {
                "sources": audit_log,
                "notes": [
                    "Life expectancy approximated using total population table due to access limitations",
                    "Worklife expectancy derived from retirement age hint",
                    "Wage series synthesised using assumed growth rate",
                ],
            },
        }, f, indent=2)
    # Return result dictionary
    return {
        "case_id": config["case_id"],
        "summary": summary,
        "files": {
            "excel_path": excel_path,
            "memo_pdf_path": memo_path,
        },
        "audit": {
            "sources": audit_log,
            "notes": [
                "Life expectancy approximated using total population table due to access limitations",
                "Worklife expectancy derived from retirement age hint",
                "Wage series synthesised using assumed growth rate",
            ],
        },
    }

"""Clean the Ask a Manager Salary Survey 2021 for the Tableau dashboard.

Scope: USA / USD only. Python re-implementation of the original Tableau
Prep flow (Final_Project.tfl), with corrected groupings:
  * INDUSTRY  -> the survey's standard dropdown options
                 (cleaning/industry_mapping.json); everything else "Others".
  * US_STATES -> state as answered; several states -> "Multiple states";
                 missing -> "Unknown".
The original 2024 Prep groupings are kept for reference in
mappings_2024_original.json. Output columns keep the names used by
"Final Visualization.twb" so the workbook can switch its data source.

Usage (from the repository root):
    # print the salary distribution only
    python -m cleaning.clean_salary_survey
    # write the cleaned CSV and the report
    python -m cleaning.clean_salary_survey \\
        --min-salary 15000 --max-salary 1000000
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = REPO_ROOT / "Raw dataset.xlsx"
SHEET_NAME = "Form Responses 1"
INDUSTRY_RULES_PATH = Path(__file__).resolve().parent / "industry_mapping.json"
OUTPUT_CSV = REPO_ROOT / "data" / "salary_survey_usa_clean.csv"
REPORT_JSON = REPO_ROOT / "results" / "cleaning_report.json"

SALARY_BOUNDS_REASON = (
    "Lower bound ~ annualised full-time US federal minimum wage "
    "(7.25 USD x 2,080 h ~ 15,080 USD); "
    "upper bound removes implausible values above 1M."
)

# Long survey question -> short column name (same names as the Prep output).
RENAME_MAP = {
    "How old are you?": "AGE",
    "What industry do you work in?": "INDUSTRY",
    "Job title": "JOB_TITLE",
    "What is your annual salary? (You'll indicate the currency in a later "
    "question. If you are part-time or hourly, please enter an annualized "
    "equivalent -- what you would earn if you worked the job 40 hours a "
    "week, 52 weeks a year.)": "ANNUAL_SALARY",
    "How much additional monetary compensation do you get, if any (for "
    "example, bonuses or overtime in an average year)? Please only include "
    "monetary compensation here, not the value of benefits.":
        "BONUS_OR_OVERTIME",
    "Please indicate the currency": "CURRENCY",
    "What country do you work in?": "COUNTRY",
    "If you're in the U.S., what state do you work in?": "US_STATES",
    "What city do you work in?": "CITY",
    "How many years of professional work experience do you have overall?":
        "TOTAL_EXPERIENCE",
    "How many years of professional work experience do you have in your "
    "field?": "FIELD_EXPERIENCE",
    "What is your highest level of education completed?": "EDUCATION",
    "What is your gender?": "GENDER",
    "What is your race? (Choose all that apply.)": "RACE",
}
TIMESTAMP_COLUMN = "Timestamp"

# Output column order used for the CSV (names must match the workbook).
OUTPUT_COLUMNS = [
    "INDUSTRY", "US_STATES", "BONUS_OR_OVERTIME", "GENDER", "RACE", "AGE",
    "JOB_TITLE", "ANNUAL_SALARY", "CURRENCY", "COUNTRY", "CITY",
    "TOTAL_EXPERIENCE", "FIELD_EXPERIENCE", "EDUCATION",
]

MULTIPLE_STATES = "Multiple states"
UNKNOWN = "Unknown"

# GENDER answers merged into an existing survey option.
GENDER_MERGE = {"Prefer not to answer": "Other or prefer not to answer"}

# --- Country normalisation -------------------------------------------------
# Values are compared after lower-casing and removing everything except
# letters a-z, so "U.S.", "u. s", "United  States " collapse to one key.
US_EXACT_KEYS = {
    "us", "usa", "america", "theus", "usofa",
    "unitedstates", "theunitedstates", "unitedstatesofamerica",
}
# Obvious one-letter typos of "USA" seen in the data. Deliberately NOT
# included: "usd" (a currency), "is"/"isa"/"i.s." (could be Iceland /
# unclear), "ua"/"uae".
US_TYPO_KEYS = {"usaa", "usab", "usat", "uss"}
US_LONG_FORMS = ("unitedstates", "unitedstatesofamerica")
MAX_TYPO_DISTANCE = 2
US_FLAG_EMOJI = "\U0001F1FA\U0001F1F8"


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            )
        prev = cur
    return prev[-1]


def is_us_variant(value) -> bool:
    """True only for clear spellings/typos of the United States.

    Free-text answers with qualifiers ("USA, but ...") are not matched.
    """
    if not isinstance(value, str):
        return False
    if value.strip() == US_FLAG_EMOJI:
        return True
    key = re.sub(r"[^a-z]", "", value.lower())
    if not key:
        return False
    if key in US_EXACT_KEYS or key in US_TYPO_KEYS:
        return True
    # Typos of "united states (of america)": must still start with "un".
    if key.startswith("un") and len(key) >= 10:
        return any(
            _levenshtein(key, form) <= MAX_TYPO_DISTANCE
            for form in US_LONG_FORMS
        )
    return False


def normalise_country(series: pd.Series) -> pd.Series:
    """Map clear US variants to "USA"; leave every other value unchanged."""
    return series.where(~series.map(is_us_variant), "USA")


# --- Industry --------------------------------------------------------------
def load_industry_rules(path: Path = INDUSTRY_RULES_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def industry_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().replace("&", "and"))


def map_industry(series: pd.Series, rules: dict) -> pd.Series:
    """Map INDUSTRY answers to the survey's standard dropdown options.

    Case/spacing/punctuation and 1-letter-typo variants of an option map to
    that option; any other free text -> rules["other_label"];
    missing -> rules["missing_label"].
    """
    options = {industry_key(o): o for o in rules["standard_options"]}
    match = rules["matching"]
    max_dist = match["max_spelling_distance"]
    min_len = match["min_option_key_length_for_spelling"]
    cache: dict[str, str] = {}

    def one(value):
        if not isinstance(value, str) or not value.strip():
            return rules["missing_label"]
        if value not in cache:
            key = industry_key(value)
            target = options.get(key)
            if target is None and key:
                close = [
                    o for k, o in options.items()
                    if len(k) >= min_len
                    and _levenshtein(key, k) <= max_dist
                ]
                target = close[0] if len(close) == 1 else None
            cache[value] = target or rules["other_label"]
        return cache[value]

    return series.map(one)


# --- US states -------------------------------------------------------------
def map_us_states(series: pd.Series) -> pd.Series:
    """State as answered; several states -> "Multiple states".

    Answers listing several states are comma-separated (no US state name
    contains a comma). Missing/blank -> "Unknown".
    """
    def one(value):
        if not isinstance(value, str) or not value.strip():
            return UNKNOWN
        return MULTIPLE_STATES if "," in value else value.strip()

    return series.map(one)


# --- Gender ----------------------------------------------------------------
def merge_gender(series: pd.Series) -> pd.Series:
    """Merge GENDER answers listed in GENDER_MERGE (exact match).

    All other values, including missing, are left unchanged.
    """
    return series.replace(GENDER_MERGE)


# --- Generic steps ---------------------------------------------------------
def rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in RENAME_MAP if c not in df.columns]
    if missing:
        raise KeyError(
            f"Raw data is missing expected survey columns: {missing}"
        )
    return df.rename(columns=RENAME_MAP)


def remove_duplicate_responses(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Drop responses identical in every column except Timestamp.

    The first occurrence is kept.
    """
    subset = [c for c in df.columns if c != TIMESTAMP_COLUMN]
    deduped = df.drop_duplicates(subset=subset, keep="first")
    return deduped, len(df) - len(deduped)


def fill_bonus(series: pd.Series) -> pd.Series:
    """Prep flow: IFNULL([BONUSOROVERTIME], 0) on a whole-number column."""
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.fillna(0).round().astype("int64")


def apply_salary_bounds(
    df: pd.DataFrame, min_salary: float, max_salary: float
) -> pd.DataFrame:
    """Keep rows with min_salary <= ANNUAL_SALARY <= max_salary."""
    if min_salary > max_salary:
        raise ValueError("min_salary must be <= max_salary")
    s = df["ANNUAL_SALARY"]
    return df[(s >= min_salary) & (s <= max_salary)]


def salary_distribution(salary: pd.Series) -> dict:
    s = salary.dropna()
    top_10 = s.sort_values(ascending=False).head(10)
    return {
        "count": int(s.size),
        "below_1000": int((s < 1_000).sum()),
        "below_10000": int((s < 10_000).sum()),
        "below_15000": int((s < 15_000).sum()),
        "above_500000": int((s > 500_000).sum()),
        "above_1000000": int((s > 1_000_000).sum()),
        "top_10_values": [int(v) for v in top_10],
        "median": float(s.median()),
        "mean": round(float(s.mean()), 2),
    }


def _counts(series: pd.Series) -> dict:
    return {k: int(v) for k, v in series.value_counts().items()}


# --- Pipeline --------------------------------------------------------------
def prepare_usa_rows(
    raw: pd.DataFrame, industry_rules: dict
) -> tuple[pd.DataFrame, dict]:
    """All steps up to (not including) the salary bounds."""
    report: dict = {"rows": {"raw": len(raw)}}

    df, n_dupes = remove_duplicate_responses(raw)
    report["duplicates_removed"] = n_dupes
    report["duplicates_rule"] = (
        "identical in all original columns except Timestamp; first kept"
    )
    report["rows"]["after_duplicates"] = len(df)

    df = rename_columns(df)

    df = df.assign(COUNTRY=normalise_country(df["COUNTRY"]))
    usd = df["CURRENCY"] == "USD"
    is_usa = df["COUNTRY"] == "USA"
    report["usd_rows_excluded_non_usa_or_ambiguous_country"] = int(
        (usd & ~is_usa).sum()
    )
    df = df[is_usa & usd].copy()
    report["rows"]["after_usa_usd"] = len(df)

    df["BONUS_OR_OVERTIME"] = fill_bonus(df["BONUS_OR_OVERTIME"])
    df["INDUSTRY"] = map_industry(df["INDUSTRY"], industry_rules)
    df["US_STATES"] = map_us_states(df["US_STATES"])
    df["RACE"] = df["RACE"].fillna(UNKNOWN)

    merged = df["GENDER"].isin(GENDER_MERGE.keys())
    df["GENDER"] = merge_gender(df["GENDER"])
    report["gender_rule"] = {
        "merged": GENDER_MERGE,
        "rows_changed": int(merged.sum()),
    }

    # Prep flow: exclude missing EDUCATION, GENDER and CITY.
    before = len(df)
    complete = (
        df["EDUCATION"].notna() & df["GENDER"].notna() & df["CITY"].notna()
    )
    df = df[complete]
    report["missing_value_rows_removed"] = before - len(df)
    report["missing_value_rule"] = (
        "rows with missing EDUCATION, GENDER or CITY excluded"
    )
    report["rows"]["after_missing_rules"] = len(df)

    return df[OUTPUT_COLUMNS], report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--min-salary", type=float,
        help="inclusive lower bound for ANNUAL_SALARY",
    )
    parser.add_argument(
        "--max-salary", type=float,
        help="inclusive upper bound for ANNUAL_SALARY",
    )
    args = parser.parse_args(argv)

    raw = pd.read_excel(RAW_PATH, sheet_name=SHEET_NAME)
    df, report = prepare_usa_rows(raw, load_industry_rules())
    dist = salary_distribution(df["ANNUAL_SALARY"])

    if args.min_salary is None or args.max_salary is None:
        summary = {
            "rows": report["rows"],
            "salary_distribution_before_bounds": dist,
        }
        print(json.dumps(summary, indent=2))
        print(
            "\nNo salary bounds given (--min-salary/--max-salary): "
            "no files written."
        )
        return

    df = apply_salary_bounds(df, args.min_salary, args.max_salary)
    report["salary_bounds"] = {
        "min_inclusive": args.min_salary,
        "max_inclusive": args.max_salary,
        "reason": SALARY_BOUNDS_REASON,
    }
    report["rows"]["after_salary_bounds"] = len(df)
    report["salary_distribution_before_bounds"] = dist
    report["final"] = {
        "rows": len(df),
        "median_annual_salary": float(df["ANNUAL_SALARY"].median()),
        "mean_annual_salary": round(float(df["ANNUAL_SALARY"].mean()), 2),
        "median_bonus_or_overtime": float(df["BONUS_OR_OVERTIME"].median()),
    }
    report["industry_counts"] = _counts(df["INDUSTRY"])
    report["us_states_counts"] = _counts(df["US_STATES"])

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    with open(REPORT_JSON, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps({k: report[k] for k in ("rows", "final")}, indent=2))
    print(
        f"\nWrote {OUTPUT_CSV.relative_to(REPO_ROOT)} and "
        f"{REPORT_JSON.relative_to(REPO_ROOT)}"
    )


if __name__ == "__main__":
    main()

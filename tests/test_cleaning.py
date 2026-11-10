import numpy as np
import pandas as pd
import pytest

from cleaning.clean_salary_survey import (
    MULTIPLE_STATES,
    OUTPUT_COLUMNS,
    RENAME_MAP,
    UNKNOWN,
    apply_salary_bounds,
    fill_bonus,
    load_industry_rules,
    map_industry,
    map_us_states,
    normalise_country,
    prepare_usa_rows,
    remove_duplicate_responses,
    rename_columns,
)

RULES = {
    "standard_options": [
        "Computing or Tech", "Law", "Nonprofits", "Health care",
        "Accounting, Banking & Finance",
    ],
    "matching": {
        "max_spelling_distance": 1,
        "min_option_key_length_for_spelling": 8,
    },
    "other_label": "Others",
    "missing_label": "Others",
}

# ---------------------------------------------------------------- rename


def test_rename_produces_short_names():
    raw = pd.DataFrame({long: ["x"] for long in RENAME_MAP})
    renamed = rename_columns(raw)
    assert set(renamed.columns) == set(OUTPUT_COLUMNS)


def test_rename_keeps_values_and_extra_columns():
    raw = pd.DataFrame({long: [i] for i, long in enumerate(RENAME_MAP)})
    raw["Timestamp"] = ["t"]
    renamed = rename_columns(raw)
    assert renamed.loc[0, "AGE"] == 0
    assert renamed.loc[0, "RACE"] == len(RENAME_MAP) - 1
    assert "Timestamp" in renamed.columns


def test_rename_fails_loudly_if_question_missing():
    raw = pd.DataFrame({long: ["x"] for long in list(RENAME_MAP)[1:]})
    with pytest.raises(KeyError):
        rename_columns(raw)


# ---------------------------------------------------------------- country


@pytest.mark.parametrize(
    "value",
    [
        "US", "us", "U.S.", "U. S.", "U.S", "USA", "Usa", "U.S.A.",
        "U.S.A", " USA ", "United States", "united states ",
        "United  States", "United States of America", "The United States",
        "America", "US of A", "\U0001F1FA\U0001F1F8",
        # typos
        "United Sates", "Untied States", "Unites States", "United Statea",
        "United Stated", "USAA", "USS",
    ],
)
def test_us_variants_become_usa(value):
    assert normalise_country(pd.Series([value])).iloc[0] == "USA"


@pytest.mark.parametrize(
    "value",
    [
        "Cambodia", "Canada", "United Kingdom", "UK",
        "United Arab Emirates", "UAE", "Australia", "Austria", "Bulgaria",
        "Colombia", "Chile", "Puerto Rico", "USD", "IS", "ISA",
        "California", "San Francisco", "USA, but for foreign gov't",
        "I am located in Canada but I work for a company in the US",
    ],
)
def test_non_us_values_are_untouched(value):
    assert normalise_country(pd.Series([value])).iloc[0] == value


def test_country_missing_value_stays_missing():
    out = normalise_country(pd.Series([None, "US"]))
    assert pd.isna(out.iloc[0]) and out.iloc[1] == "USA"


# ---------------------------------------------------------------- duplicates


def test_duplicates_ignore_timestamp_and_keep_first():
    df = pd.DataFrame(
        {
            "Timestamp": ["t1", "t2", "t3", "t4"],
            "a": [1, 1, 2, 1],
            "b": ["x", "x", "x", np.nan],
        }
    )
    out, n = remove_duplicate_responses(df)
    assert n == 1
    # row 1 duplicates row 0 apart from Timestamp -> dropped; rows 2, 3 differ
    assert list(out.index) == [0, 2, 3]
    assert out.loc[0, "Timestamp"] == "t1"


def test_duplicates_treat_nan_as_equal():
    df = pd.DataFrame(
        {"Timestamp": ["t1", "t2"], "a": [1, 1], "b": [np.nan, np.nan]}
    )
    out, n = remove_duplicate_responses(df)
    assert n == 1 and len(out) == 1


# ---------------------------------------------------------------- bonus


def test_fill_bonus_missing_to_zero_integer():
    out = fill_bonus(pd.Series([np.nan, 5000.0, 0.0, None]))
    assert out.tolist() == [0, 5000, 0, 0]
    assert out.dtype == "int64"


# ---------------------------------------------------------------- industry


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Computing or Tech", "Computing or Tech"),
        ("computing or tech", "Computing or Tech"),
        (" Computing  or Tech ", "Computing or Tech"),
        ("Accounting, Banking and Finance", "Accounting, Banking & Finance"),
        ("Healthcare", "Health care"),
        ("Nonprofit", "Nonprofits"),  # 1-letter spelling variant
        ("Law", "Law"),
        ("Lab", "Others"),  # short option: no fuzzy matching
        ("Publishing", "Others"),
        ("Healthcare IT", "Others"),
        (None, "Others"),
        (np.nan, "Others"),
        ("   ", "Others"),
    ],
)
def test_map_industry(value, expected):
    series = pd.Series([value], dtype=object)
    assert map_industry(series, RULES).iloc[0] == expected


def test_industry_rules_file_has_26_unique_options():
    rules = load_industry_rules()
    assert len(rules["standard_options"]) == 26
    assert len(set(rules["standard_options"])) == 26


# ---------------------------------------------------------------- US states


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Ohio", "Ohio"),
        ("District of Columbia", "District of Columbia"),
        ("Kentucky", "Kentucky"),
        ("District of Columbia, Maryland, Virginia", MULTIPLE_STATES),
        ("Iowa, Nebraska", MULTIPLE_STATES),
        (None, UNKNOWN),
        (np.nan, UNKNOWN),
    ],
)
def test_map_us_states(value, expected):
    series = pd.Series([value], dtype=object)
    assert map_us_states(series).iloc[0] == expected


# ------------------------------------------------------------- salary bounds


def test_salary_bounds_are_inclusive():
    df = pd.DataFrame(
        {"ANNUAL_SALARY": [14999, 15000, 50000, 1_000_000, 1_000_001]}
    )
    out = apply_salary_bounds(df, 15000, 1_000_000)
    assert out["ANNUAL_SALARY"].tolist() == [15000, 50000, 1_000_000]


def test_salary_bounds_reject_inverted_range():
    with pytest.raises(ValueError):
        apply_salary_bounds(pd.DataFrame({"ANNUAL_SALARY": [1]}), 10, 1)


# ---------------------------------------------------------------- end to end

JOB_CONTEXT_Q = (
    "If your job title needs additional context, please clarify here:"
)
OTHER_CURRENCY_Q = 'If "Other," please indicate the currency here: '
INCOME_CONTEXT_Q = (
    "If your income needs additional context, please provide it here:"
)


def _raw_row(timestamp="2021-04-27 11:02:00", **overrides):
    row = {
        "Timestamp": timestamp,
        JOB_CONTEXT_Q: None,
        OTHER_CURRENCY_Q: None,
        INCOME_CONTEXT_Q: None,
    }
    short_values = {
        "AGE": "25-34",
        "INDUSTRY": "Computing or Tech",
        "JOB_TITLE": "Analyst",
        "ANNUAL_SALARY": 70000,
        "BONUS_OR_OVERTIME": np.nan,
        "CURRENCY": "USD",
        "COUNTRY": "U.S.",
        "US_STATES": "Ohio",
        "CITY": "Columbus",
        "TOTAL_EXPERIENCE": "5-7 years",
        "FIELD_EXPERIENCE": "2 - 4 years",
        "EDUCATION": "College degree",
        "GENDER": "Woman",
        "RACE": "White",
    }
    short_values.update(overrides)
    long_of = {short: long for long, short in RENAME_MAP.items()}
    row.update({long_of[k]: v for k, v in short_values.items()})
    return row


def test_prepare_usa_rows_end_to_end():
    raw = pd.DataFrame(
        [
            _raw_row(),
            # duplicate apart from Timestamp
            _raw_row(timestamp="2021-04-27 11:05:00"),
            _raw_row(COUNTRY="Canada"),
            _raw_row(CURRENCY="CAD"),
            _raw_row(GENDER=np.nan),
            _raw_row(EDUCATION=np.nan),
            _raw_row(CITY=np.nan),
            _raw_row(
                INDUSTRY=np.nan, US_STATES=np.nan, RACE=np.nan,
                JOB_TITLE="Other",
            ),
            _raw_row(
                INDUSTRY="Zoo", US_STATES="Maryland, Virginia",
                JOB_TITLE="Keeper",
            ),
        ]
    )
    out, report = prepare_usa_rows(raw, RULES)
    assert report["rows"] == {
        "raw": 9,
        "after_duplicates": 8,
        "after_usa_usd": 6,
        "after_missing_rules": 3,
    }
    assert report["duplicates_removed"] == 1
    assert report["usd_rows_excluded_non_usa_or_ambiguous_country"] == 1
    assert list(out.columns) == OUTPUT_COLUMNS
    assert (out["COUNTRY"] == "USA").all()
    assert out["BONUS_OR_OVERTIME"].tolist() == [0, 0, 0]
    by_title = out.set_index("JOB_TITLE")
    assert by_title.loc["Analyst", "INDUSTRY"] == "Computing or Tech"
    assert by_title.loc["Other", "INDUSTRY"] == "Others"
    assert by_title.loc["Other", "US_STATES"] == UNKNOWN
    assert by_title.loc["Other", "RACE"] == UNKNOWN
    assert by_title.loc["Keeper", "INDUSTRY"] == "Others"
    assert by_title.loc["Keeper", "US_STATES"] == MULTIPLE_STATES

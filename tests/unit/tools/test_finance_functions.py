"""Unit tests for the canonical finance function implementations."""

import pytest

from trellis.models.handles import PeriodDescriptor
from trellis.registry.finance_functions import (
    build_finance_registry,
    financial_scale_normalize,
    fiscal_period_logic,
    fiscal_year_end,
    period_label,
    ticker_lookup,
)


# ---------------------------------------------------------------------------
# fiscal_period_logic
# ---------------------------------------------------------------------------


class TestFiscalPeriodLogic:
    def test_calendar_year_end_returns_single_annual(self):
        results = fiscal_period_logic("2024-12-31", "amazon")
        assert len(results) == 1
        assert results[0].is_annual is True
        assert results[0].period_type == "annual"
        assert results[0].period_end == "2024-12-31"

    def test_non_year_end_returns_three_periods(self):
        results = fiscal_period_logic("2024-03-31", "amazon")
        assert len(results) == 3
        types = {p.period_type for p in results}
        assert types == {"annual", "ytd_current", "ytd_prior"}

    def test_apple_fiscal_year_end(self):
        # Apple FYE = Sep 30
        results = fiscal_period_logic("2024-09-30", "apple")
        assert len(results) == 1
        assert results[0].is_annual is True
        assert results[0].period_end == "2024-09-30"

    def test_apple_interim_period(self):
        results = fiscal_period_logic("2024-03-31", "apple")
        assert len(results) == 3

    def test_period_descriptors_are_correct_type(self):
        results = fiscal_period_logic("2024-06-30", "microsoft")
        for p in results:
            assert isinstance(p, PeriodDescriptor)

    def test_ytd_current_uses_input_date(self):
        results = fiscal_period_logic("2024-06-30", "amazon")
        ytd = next(p for p in results if p.period_type == "ytd_current")
        assert ytd.period_end == "2024-06-30"

    def test_prior_ytd_is_one_year_back(self):
        results = fiscal_period_logic("2024-06-30", "amazon")
        prior = next(p for p in results if p.period_type == "ytd_prior")
        assert prior.period_end == "2023-06-30"

    def test_label_contains_year(self):
        results = fiscal_period_logic("2024-12-31", "amazon")
        assert "2024" in results[0].label


# ---------------------------------------------------------------------------
# ticker_lookup
# ---------------------------------------------------------------------------


class TestTickerLookup:
    def test_exact_match(self):
        assert ticker_lookup("apple") == "AAPL"
        assert ticker_lookup("microsoft") == "MSFT"
        assert ticker_lookup("amazon") == "AMZN"

    def test_case_insensitive(self):
        assert ticker_lookup("Apple") == "AAPL"
        assert ticker_lookup("MICROSOFT") == "MSFT"

    def test_substring_match(self):
        assert ticker_lookup("Apple Inc.") == "AAPL"

    def test_unknown_company_raises(self):
        with pytest.raises(ValueError, match="No ticker mapping"):
            ticker_lookup("XYZ Corp Unknown")

    def test_google_alias(self):
        assert ticker_lookup("google") == "GOOGL"
        assert ticker_lookup("alphabet") == "GOOGL"


# ---------------------------------------------------------------------------
# financial_scale_normalize
# ---------------------------------------------------------------------------


class TestFinancialScaleNormalize:
    def test_usd_to_usd_millions(self):
        result = financial_scale_normalize(
            value=1_000_000,
            source_currency="USD",
            target_currency="USD",
            target_scale="millions",
        )
        assert result == pytest.approx(1.0)

    def test_usd_to_usd_thousands(self):
        result = financial_scale_normalize(
            value=500_000,
            source_currency="USD",
            target_currency="USD",
            target_scale="thousands",
        )
        assert result == pytest.approx(500.0)

    def test_usd_ones(self):
        result = financial_scale_normalize(
            value=42.0,
            source_currency="USD",
            target_currency="USD",
            target_scale="ones",
        )
        assert result == pytest.approx(42.0)

    def test_unknown_source_currency_raises(self):
        with pytest.raises(ValueError, match="Unknown source currency"):
            financial_scale_normalize(1000, "XYZ", "USD", "millions")

    def test_unknown_target_currency_raises(self):
        with pytest.raises(ValueError, match="Unknown target currency"):
            financial_scale_normalize(1000, "USD", "XYZ", "millions")

    def test_unknown_scale_raises(self):
        with pytest.raises(ValueError, match="Unknown target scale"):
            financial_scale_normalize(1000, "USD", "USD", "centillions")

    def test_string_value_coerced(self):
        result = financial_scale_normalize("1000000", "USD", "USD", "millions")
        assert result == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# period_label
# ---------------------------------------------------------------------------


class TestPeriodLabel:
    def test_annual(self):
        assert period_label("2024-12-31", "annual") == "FY 2024"

    def test_fy_alias(self):
        assert period_label("2024-12-31", "fy") == "FY 2024"

    def test_quarterly(self):
        assert period_label("2024-03-31", "quarterly") == "Q1 2024"
        assert period_label("2024-06-30", "quarterly") == "Q2 2024"
        assert period_label("2024-09-30", "quarterly") == "Q3 2024"
        assert period_label("2024-12-31", "quarterly") == "Q4 2024"

    def test_ytd_current(self):
        assert period_label("2025-03-31", "ytd_current") == "Q1 2025"

    def test_half_year(self):
        assert period_label("2024-06-30", "half_year") == "H1 2024"
        assert period_label("2024-12-31", "half_year") == "H2 2024"

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown period_type"):
            period_label("2024-01-31", "monthly")


# ---------------------------------------------------------------------------
# fiscal_year_end
# ---------------------------------------------------------------------------


class TestFiscalYearEnd:
    def test_apple(self):
        assert fiscal_year_end("apple") == "09-30"

    def test_microsoft(self):
        assert fiscal_year_end("microsoft") == "06-30"

    def test_default_calendar_year(self):
        assert fiscal_year_end("some unknown company") == "12-31"

    def test_walmart(self):
        assert fiscal_year_end("walmart") == "01-31"

    def test_case_insensitive(self):
        assert fiscal_year_end("Apple Inc") == "09-30"


# ---------------------------------------------------------------------------
# build_finance_registry
# ---------------------------------------------------------------------------


class TestBuildFinanceRegistry:
    def test_all_canonical_functions_registered(self):
        reg = build_finance_registry()
        expected = {
            "fiscal_period_logic",
            "ticker_lookup",
            "financial_scale_normalize",
            "period_label",
            "fiscal_year_end",
        }
        assert expected.issubset(set(reg.names()))

    def test_no_duplicate_registration(self):
        # Two separate builds should not raise
        reg1 = build_finance_registry()
        reg2 = build_finance_registry()
        assert reg1.names() == reg2.names()

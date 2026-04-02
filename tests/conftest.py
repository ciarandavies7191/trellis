"""
tests/conftest.py — Shared fixtures for the Trelis test suite.

Fixtures are organized by DSL level:
  - Raw YAML strings  (suffix _yaml)
  - Parsed model objects (no suffix — built from the YAML fixtures)

Keeping YAML and model fixtures separate lets individual test modules
import just the YAML when they want to test parsing, or the model
directly when they want to test behaviour.
"""

from __future__ import annotations

import pytest

from trellis.models.pipeline import Pipeline
from trellis.models.plan import Plan


# ---------------------------------------------------------------------------
# Plan YAML fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gulf_plan_yaml() -> str:
    """Full Gulf disruption assessment plan from DSL v1.3 spec."""
    return """
plan:
  id: gulf_disruption_assessment
  goal: >
    Assess disruption, impact, and credit risk across three energy disruption scenarios.
  inputs:
    companies: [Google, Apple, Microsoft, Agilent]
    year: 2025
  sub_pipelines:
    - id: data_acquisition
      goal: "Fetch company financials, energy exposure, market data, and web context"
      reads: []
      stores: [company_financials, energy_exposure, energy_markets, disruption_context]
      inputs: [companies, year]
    - id: short_term_assessment
      goal: "Assess disruption, impact and credit for 1-month risk premium shock"
      reads: [company_financials, energy_exposure, energy_markets, disruption_context]
      stores: [credit_short, impact_short]
      inputs: [companies]
    - id: medium_term_assessment
      goal: "Assess disruption, impact and credit for 1-3 month supply constraint"
      reads: [company_financials, energy_exposure, energy_markets, disruption_context, credit_short]
      stores: [credit_medium, impact_medium]
      inputs: [companies]
    - id: long_term_assessment
      goal: "Assess disruption, impact and credit for >6 month sustained disruption"
      reads: [company_financials, energy_exposure, energy_markets, disruption_context, credit_medium]
      stores: [credit_long, impact_long]
      inputs: [companies]
    - id: synthesis
      goal: "Synthesise analysis across all three scenario horizons"
      reads: [company_financials, energy_exposure,
              credit_short, credit_medium, credit_long,
              impact_short, impact_medium, impact_long]
      stores: [liquidity_analysis, supply_chain_analysis, energy_cost_analysis]
      inputs: [companies]
    - id: final_report
      goal: "Produce executive credit assessment report"
      reads: [liquidity_analysis, supply_chain_analysis, energy_cost_analysis]
      stores: []
      inputs: []
"""


@pytest.fixture
def parallel_plan_yaml() -> str:
    """Plan with two independent root sub-pipelines that can run in parallel."""
    return """
plan:
  id: parallel_plan
  goal: "Fetch data from two independent sources then synthesise"
  inputs:
    companies: [Google, Apple]
  sub_pipelines:
    - id: fetch_financials
      goal: "Fetch financials"
      reads: []
      stores: [financials]
      inputs: [companies]
    - id: fetch_market_data
      goal: "Fetch market data"
      reads: []
      stores: [market_data]
      inputs: []
    - id: synthesis
      goal: "Synthesise financials and market data"
      reads: [financials, market_data]
      stores: [report]
      inputs: [companies]
"""


@pytest.fixture
def minimal_plan_yaml() -> str:
    """Smallest valid plan — single sub-pipeline, no inputs."""
    return """
plan:
  id: minimal_plan
  goal: "Minimal plan for testing"
  sub_pipelines:
    - id: only_pipeline
      goal: "Do the one thing"
      reads: []
      stores: [result]
"""


# ---------------------------------------------------------------------------
# Pipeline YAML fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def data_acquisition_yaml() -> str:
    """data_acquisition pipeline from DSL v1.3 spec — 4 parallel roots."""
    return """
pipeline:
  id: data_acquisition
  goal: "Fetch company financials, energy exposure, market data, and web context"
  inputs:
    companies: [Google, Apple, Microsoft, Agilent]
    year: 2025
  tasks:
    - id: fetch_financials
      tool: fetch_data
      inputs:
        source: company_info
        companies: "{{pipeline.inputs.companies}}"
        fields: [liquidity_ratios, debt_structure, credit_ratings]
        year: "{{pipeline.inputs.year}}"
    - id: fetch_energy_exposure
      tool: fetch_data
      inputs:
        source: company_info
        companies: "{{pipeline.inputs.companies}}"
        fields: [energy_cost_pct_revenue, supply_chain_geography]
    - id: fetch_energy_markets
      tool: fetch_data
      inputs:
        source: market_data
        markets: [crude_oil, natural_gas, energy_risk_premium]
        fields: [price, volatility, forward_curve]
    - id: search_disruption_context
      tool: search_web
      inputs:
        query: "Gulf energy export disruption logistics bottleneck 2025"
    - id: store_financials
      tool: store
      inputs:
        key: company_financials
        value: "{{fetch_financials.output}}"
    - id: store_energy_exposure
      tool: store
      inputs:
        key: energy_exposure
        value: "{{fetch_energy_exposure.output}}"
    - id: store_energy_markets
      tool: store
      inputs:
        key: energy_markets
        value: "{{fetch_energy_markets.output}}"
    - id: store_disruption_context
      tool: store
      inputs:
        key: disruption_context
        value: "{{search_disruption_context.output}}"
"""


@pytest.fixture
def linear_pipeline_yaml() -> str:
    """Fully sequential pipeline — each task depends on the previous."""
    return """
pipeline:
  id: linear_pipeline
  goal: "Linear three-step pipeline"
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
    - id: summarise
      tool: llm_job
      inputs:
        data: "{{fetch.output}}"
        prompt: "Summarise the key financial metrics"
    - id: export_report
      tool: export
      inputs:
        content: "{{summarise.output}}"
        format: markdown
        filename: "summary_report"
"""


@pytest.fixture
def fan_out_pipeline_yaml() -> str:
    """Pipeline with parallel_over fan-out and an await barrier."""
    return """
pipeline:
  id: fan_out_pipeline
  goal: "Assess credit risk per company with audit logging"
  inputs:
    companies: [Google, Apple, Microsoft]
  tasks:
    - id: fetch_financials
      tool: fetch_data
      inputs:
        source: company_info
        companies: "{{pipeline.inputs.companies}}"
    - id: audit_log
      tool: llm_job
      inputs:
        data: "{{fetch_financials.output}}"
        prompt: "Write audit log entry for this data fetch"
    - id: assess_per_company
      tool: llm_job
      parallel_over: "{{pipeline.inputs.companies}}"
      await: [audit_log]
      inputs:
        company: "{{item}}"
        financials: "{{fetch_financials.output}}"
        prompt: "Assess credit risk for {{item}} under current scenario"
    - id: synthesise
      tool: llm_job
      inputs:
        assessments: "{{assess_per_company.output}}"
        prompt: "Synthesise all per-company assessments into a summary"
"""


@pytest.fixture
def document_pipeline_yaml() -> str:
    """Pipeline exercising load_document → select → extract_table → llm_job → export."""
    return """
pipeline:
  id: document_pipeline
  goal: "Extract and analyse financial tables from an uploaded report"
  inputs:
    report_path: "/data/annual_report.pdf"
  tasks:
    - id: ingest_report
      tool: load_document
      inputs:
        path: "{{pipeline.inputs.report_path}}"
    - id: select_financial_pages
      tool: select
      inputs:
        document: "{{ingest_report.output}}"
        prompt: "Pages containing financial tables, balance sheets, or annual projections"
    - id: extract_tables
      tool: extract_table
      inputs:
        document: "{{select_financial_pages.output}}"
        selector: "income statement"
    - id: extract_notes
      tool: extract_text
      inputs:
        document: "{{ingest_report.output}}"
        selector: "notes to financial statements"
    - id: reconcile
      tool: llm_job
      inputs:
        tables: "{{extract_tables.output}}"
        notes: "{{extract_notes.output}}"
        prompt: "Identify discrepancies between the tables and the notes"
    - id: produce_report
      tool: export
      inputs:
        content: "{{reconcile.output}}"
        format: markdown
        filename: "reconciliation_report"
"""


# ---------------------------------------------------------------------------
# Parsed model fixtures (built from YAML fixtures above)
# ---------------------------------------------------------------------------


@pytest.fixture
def gulf_plan(gulf_plan_yaml: str) -> Plan:
    return Plan.from_yaml(gulf_plan_yaml)


@pytest.fixture
def parallel_plan(parallel_plan_yaml: str) -> Plan:
    return Plan.from_yaml(parallel_plan_yaml)


@pytest.fixture
def data_acquisition_pipeline(data_acquisition_yaml: str) -> Pipeline:
    return Pipeline.from_yaml(data_acquisition_yaml)


@pytest.fixture
def linear_pipeline(linear_pipeline_yaml: str) -> Pipeline:
    return Pipeline.from_yaml(linear_pipeline_yaml)


@pytest.fixture
def fan_out_pipeline(fan_out_pipeline_yaml: str) -> Pipeline:
    return Pipeline.from_yaml(fan_out_pipeline_yaml)


@pytest.fixture
def document_pipeline(document_pipeline_yaml: str) -> Pipeline:
    return Pipeline.from_yaml(document_pipeline_yaml)
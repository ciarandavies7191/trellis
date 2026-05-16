# Credit Due-Diligence Pipeline Suite

12-pipeline suite for underwriter credit analysis: financial spread, peer comparison,
capital structure, stress scenarios, DCF, risk assessment, and recommendation.

## Prerequisites

```bash
pip install trellis-pipelines[credit-dd]
```

Set required environment variables:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export SEC_USER_AGENT="YourApp/1.0 (you@yourfirm.com)"

# Corporate VPN proxy (if required)
export TRELLIS_HTTP_PROXY=http://127.0.0.1:9000
```

## Execution Order

```
Batch A  (sequential, human review after each)
  pipeline_01_term_sheet.yaml   ← supply deal_metadata params
  pipeline_02_financials.yaml   ← human reviews financials

Batch B  (run all six in parallel, human reviews all outputs)
  pipeline_03_obligor.yaml
  pipeline_04_peers.yaml        ← supply peer_1/peer_2/peer_3 params
  pipeline_05_capstruct.yaml
  pipeline_06_repayment.yaml
  pipeline_07_proforma.yaml
  pipeline_08_projections.yaml

Batch C  (run both in parallel, human review)
  pipeline_09_risks.yaml
  pipeline_12_rating.yaml

Batch D  (human review)
  pipeline_10_recommendation.yaml

Batch E  (auto — no review needed)
  pipeline_11_documents.yaml
```

### Full run script

```bash
# Batch A
trellis run pipeline_01_term_sheet.yaml --params '{
  "deal_metadata": {
    "ticker": "CVS",
    "borrower_name": "CVS Health Corporation",
    "facility_amount_mm": 1000,
    "facility_product": "Term Loan B",
    "facility_pricing": "SOFR+150",
    "facility_pricing_display": "SOFR + 150bps",
    "facility_spread_bps": 150,
    "facility_deal_type": "Leveraged",
    "facility_purpose": "General corporate purposes",
    "covenants": [
      {"id":"c1","name":"Gross Leverage","type":"gross_leverage","value":5.5,"direction":"max"}
    ]
  }
}'

trellis run pipeline_02_financials.yaml
# ← human reviews Step 2 output

# Batch B (parallel)
trellis run pipeline_03_obligor.yaml &
trellis run pipeline_04_peers.yaml --params '{"peer_1":"WBA","peer_2":"CI","peer_3":"UNH"}' &
trellis run pipeline_05_capstruct.yaml &
trellis run pipeline_06_repayment.yaml &
trellis run pipeline_07_proforma.yaml &
trellis run pipeline_08_projections.yaml &
wait
# ← human reviews all six outputs

# Batch C (parallel)
trellis run pipeline_09_risks.yaml &
trellis run pipeline_12_rating.yaml &
wait
# ← human reviews both

# Batch D
trellis run pipeline_10_recommendation.yaml
# ← human reviews recommendation

# Batch E (auto)
trellis run pipeline_11_documents.yaml
```

## Tools Used

| Tool | Type | Purpose |
|---|---|---|
| `fetch_sec_xbrl` | SEC | Fetch EDGAR XBRL companyfacts |
| `fetch_sec_company_profile` | SEC | Company metadata (SIC, HQ, FYE) |
| `fetch_sec_ratings` | SEC | Scan filings for credit ratings |
| `fetch_10k_sections` | SEC | Extract 10-K named sections |
| `compute` + `build_financial_spread` | Compute | 3-year financial spread from XBRL |
| `compute` + `validate_spread` | Compute | Accounting-identity checks |
| `compute` + `compute_proforma_stress` | Compute | Pro-forma, stress, covenant tests |
| `compute` + `run_dcf_model` | Compute | 5-year projection + DCF |
| `generate_documents` | Output | DOCX / PDF / PPTX / XLSX assembly |
| `llm_job` | LLM | Section authoring (Opus / Sonnet / Haiku) |
| `search_web` | Web | Supplementary research |
| `extract_fields` | Extraction | Parse structured fields from LLM output |
| `store` | Session | Persist outputs across pipelines |

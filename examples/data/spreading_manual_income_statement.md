# Income Statement Spreading Manual
## General Reference — Applicable to All Companies and Industries

**Manual Version:** 1.0  
**Applies to Template:** Income Statement Spread (10-column quarterly/annual layout)  
**Audience:** Credit analysts, equity analysts, and automated LLM extraction systems

---

## How to Use This Manual

This manual defines how to populate every field in the Income Statement Spread template. Rules are general and apply across all companies, industries, reporting currencies, and filing jurisdictions. Where treatment differs by industry or circumstance, explicit guidance is given within the rule.

Rules are written to be **deterministic and reproducible**: two analysts — or two automated extraction passes — applying this manual to the same source filing must produce identical outputs.

---

## General Rules Applicable to All Sections

| Rule | Description |
|---|---|
| **G1. Source hierarchy** | Extract values in this priority order: (1) the face of the Consolidated Statements of Income (Operations); (2) Notes to Financial Statements (revenue disaggregation note, segment information note); (3) Management's Discussion and Analysis (MD&A) revenue and segment tables. Always prefer the lowest-numbered source that contains the required figure. |
| **G2. Units** | Use the unit convention stated on the face of the income statement (e.g., USD millions, EUR thousands). Record values as plain numbers without currency symbols or formatting. Apply consistent rounding throughout: if the filing reports in millions, round all values to the nearest whole million. |
| **G3. Reporting currency** | Use the currency of the primary financial statements. Where the filing presents figures in a functional currency different from the presentation currency, use the presentation currency. Note any currency translation effects in the Analyst Notes section. |
| **G4. Period columns** | The template has ten columns: Q1, Q2, Q3, Q4, FY for two consecutive fiscal years. Label each column with the period-end date corresponding to the company's fiscal quarter. The FY column must equal the sum of Q1–Q4 for that year. If the filing provides an annual total directly, enter that figure and verify it matches the quarterly sum within rounding tolerance (±1 unit). For annual-only filers, leave Q1–Q4 blank and populate FY only. |
| **G5. Quarterly figures** | For companies that file quarterly reports (e.g., SEC 10-Q), use the quarterly period figures directly. For companies that file only annually, derive quarterly figures from interim reports or earnings releases, and note the source in the Analyst Notes section. Q4 may be computed as FY minus the Q1–Q3 year-to-date figure; note this derivation. |
| **G6. Sign convention** | Revenue and income items are entered as **positive** values. Expense items are entered as **positive** values representing the magnitude of the expense, unless a specific rule below states otherwise. Items that are inherently signed reconciling items (e.g., eliminations, contra-revenue, FX gains/losses) follow the sign convention stated in the specific rule. |
| **G7. Calculated rows** | Rows shown in *italics* in the template (Gross Profit, Gross Margin %, Operating Margin %, Effective Tax Rate %, Net Margin %, YoY Growth %) are **computed from other cells** and are never directly extracted from the filing. Calculation formulas are defined in the rule for each row. If the filing reports the same derived metric, use your calculated figure and flag discrepancies greater than rounding tolerance. |
| **G8. Not-found items** | If a line item is genuinely absent from the filing (e.g., a charge did not occur, a segment does not exist), leave the cell **blank**. Do not enter zero unless the filing explicitly states the amount was zero. Document the absence in the Analyst Notes section. |
| **G9. GAAP only** | Spread reported GAAP (or IFRS) figures only. Do not substitute non-GAAP or adjusted figures unless the template explicitly calls for an adjusted section. |
| **G10. Audited status** | Mark "Yes" if the financial statements are accompanied by an independent auditor's report. Mark "No" for unaudited interim statements (e.g., quarterly 10-Q filings). |
| **G11. Reclassifications** | If a prior-period figure has been restated or reclassified by the company, use the restated figure and note the reclassification in the Analyst Notes section. Never combine an original figure from one filing with a restated figure from another for the same company. |

---

## Header Fields

| Field | Rule |
|---|---|
| **Company** | Enter the registrant's full legal name as it appears on the cover page of the source filing. |
| **Ticker** | Enter the primary exchange ticker. For dual- or multi-class share structures, enter the ticker for the most liquid class and note the others in parentheses (e.g., `GOOGL (GOOG)`). For unlisted companies, leave blank. |
| **Prepared By** | Initials or name of the analyst preparing the spread. |
| **Date** | Date the spread was prepared (MM/DD/YYYY). |
| **Reviewed By** | Initials or name of the reviewing analyst, if applicable. |
| **Source Filing** | Filing type and period in the format: `[Form Type], [Registrant Name], [Fiscal Year or Period End]`. Example: `Form 10-K, Acme Corp., FY ended December 31, 2024`. |
| **Filed** | Date the source filing was submitted to the regulator. |
| **Reporting Currency** | Three-letter ISO code (e.g., `USD`, `EUR`, `GBP`). |
| **Units** | Unit convention as stated on the face of the income statement (e.g., `$ Millions`, `€ Thousands`). |
| **Audited** | Circle Yes or No per rule G10 above. |

---

## Section 1 — Revenue

### Overview

This section captures total revenues disaggregated by reportable business segment, plus any reconciling items. The template accommodates three named segment rows and one "Other / Eliminations" row. If the company has more than three reportable segments, combine smaller segments into row [3] and document the grouping in the Analyst Notes section. If the company has fewer than three segments, leave unused rows blank.

---

| Line Item | Definition |
|---|---|
| **1.1 Segment Revenue — [1]** | Revenues attributable to the company's first (and typically largest) reportable segment, as disclosed in the segment information note or the MD&A revenue disaggregation table. Relabel the row with the company's actual segment name. **Source:** Segment Information note or MD&A revenue table for both quarterly and annual figures. **Inclusions:** All revenues recognized by this segment under the applicable accounting standard (ASC 606 / IFRS 15), including service revenues, product revenues, licensing fees, and royalties directly attributed to the segment. **Exclusions:** Intercompany revenues that are eliminated in consolidation (capture those in row 1.4). **Sign:** Positive. |
| **1.2 Segment Revenue — [2]** | Revenues attributable to the company's second reportable segment. Relabel the row with the company's actual segment name. Apply the same sourcing and inclusion/exclusion rules as row 1.1. **Sign:** Positive. |
| **1.3 Segment Revenue — [3]** | Revenues attributable to the company's third reportable segment, or a grouped "Other Segments" total if more than three segments exist. Relabel accordingly and note any groupings in the Analyst Notes section. Apply the same sourcing rules as row 1.1. **Sign:** Positive. |
| **1.4 Other / Eliminations** | Reconciling amounts that appear in the segment revenue table but do not belong to a named segment. Includes: (a) intercompany revenue eliminations, (b) corporate revenues not allocated to segments, (c) currency hedging gains or losses on revenues when reported as a separate reconciling line, and (d) revenue reclassifications. **Sign:** Enter as a **signed number** — positive if the item adds to consolidated revenue (e.g., hedging gains), negative if it reduces consolidated revenue (e.g., intercompany eliminations, hedging losses). This is the exception to general rule G6. **Verification:** Rows 1.1 + 1.2 + 1.3 + 1.4 must equal row 1.5 for every period column. |
| **1.5 Total Revenues** | Consolidated total revenues as reported on the **face of the Consolidated Statements of Income**. Use the single top-line figure labeled "Revenues," "Total revenues," "Net revenues," or "Net sales." For companies that report "Gross revenues" and then deduct returns, allowances, or excise taxes on the face statement, use the **net figure** (after deductions). Trade returns, allowances, and sales-related taxes must be excluded. **Note:** Revenues from discontinued operations are excluded here and captured in the discontinued operations section below the tax line, if applicable. **Sign:** Positive. |
| **1.6 YoY Growth (%)** | *(Calculated)* Year-over-year percentage change in Total Revenues. **Formula:** `(Current Period Total Revenues ÷ Prior Year Same Period Total Revenues − 1) × 100`. Compare Q1-to-Q1, Q2-to-Q2, and so on — never sequential quarters across years. Round to one decimal place. Leave blank if the prior-year period is zero or unavailable. |

---

## Section 2 — Costs and Expenses

### Overview

All rows in this section are extracted from the Consolidated Statements of Income unless otherwise noted. Enter all values as **positive numbers** representing the magnitude of the expense.

---

| Line Item | Definition |
|---|---|
| **2.1 Cost of Revenues** | Direct or indirect expenditure incurred in the production or delivery of goods and services as reported on the face income statement. **Inclusions vary by industry:** (a) Manufacturing: material costs, direct labor, manufacturing overhead, factory depreciation. (b) Technology / SaaS: hosting infrastructure costs, content delivery, support costs, amortization of capitalized software used in service delivery. (c) Retail / Distribution: cost of merchandise sold, warehousing, inbound freight. (d) Media / Broadcasting: content rights amortization, film production costs, television program costs, film library and film contract costs, investments in entertainment programming. (e) Aviation / Shipping: fuel costs, maintenance, crew costs; gains or losses from fuel/derivative hedging contracts for Aviation and Shipping companies should be included here. (f) Pharma / Biotech: manufacturing costs, royalty expense on licensed products. (g) Mining / Energy: extraction costs, dry docking amortization for Shipping companies. **Exclusions:** Depreciation and amortization (if not broken out separately, D&A may be embedded here — see rows 2.5 and 2.6). Restructuring and non-recurring charges are excluded from COGS and entered in row 2.7. Non-operating losses (loss on sale of assets, etc.) are excluded and entered in the Non-Operating section. **LIFO adjustment:** If the company reports a LIFO liquidation gain, add it back to COGS (i.e., increase the COGS figure) and record a corresponding credit in Other Non-Operating Income/Expense (row 5.5). **Sign:** Positive (magnitude of expense). |
| **2.2 Gross Profit** | *(Calculated)* `Row 1.5 (Total Revenues) − Row 2.1 (Cost of Revenues)`. Do not extract from the filing even if reported; always calculate to ensure internal consistency. |
| **2.3 Gross Margin (%)** | *(Calculated)* `Row 2.2 ÷ Row 1.5 × 100`. Round to one decimal place. |
| **2.4 Research and Development** | Expense recognized for research, development, and innovation activities as labeled on the face income statement. **Inclusions:** All amounts classified by the company as research and development on the face statement. For technology, pharmaceutical, and manufacturing companies, this is typically a separately disclosed operating expense line. **Exclusions:** Capitalized development costs (these do not appear on the income statement). If the company presents R&D as a sub-line within COGS rather than as a separate operating expense, leave this row blank and note the treatment. **Sign:** Positive. |
| **2.5 Sales and Marketing** | Selling, marketing, advertising, and customer acquisition expense as labeled on the face income statement. **Inclusions:** Sales force compensation and commissions, advertising spend, promotional costs, trade shows, marketing technology, and brand spending. **Note:** Some companies label this line "Selling, general and administrative" (SG&A) as a single combined line. If SG&A is not split on the face statement, enter the combined figure in this row, leave row 2.6 blank, and note the combined presentation in Analyst Notes. Do not attempt to split SG&A from the notes unless the filing provides an explicit breakdown. **Sign:** Positive. |
| **2.6 General and Administrative** | Corporate overhead, administrative, legal, finance, human resources, and executive compensation expense as labeled on the face income statement. If presented as a combined SG&A line (see row 2.5 note), leave this row blank. **Sign:** Positive. |
| **2.7 Restructuring and Other Charges** | Charges that are separately disclosed on the face income statement and are non-recurring in nature. **Inclusions:** Restructuring charges (severance, facility closure costs), goodwill or long-lived asset impairment charges, litigation settlements recognized as operating charges, merger and integration costs, and gain or loss on disposal of a business segment if presented above the operating income line. **Exclusions:** Non-recurring items embedded within COGS, R&D, or SG&A and not separately disclosed. Do not reallocate amounts from other line items into this row; only enter amounts that appear as a distinct line on the face statement. **Sign:** Positive for charges (expense). If the company records a restructuring reversal (credit), enter as a negative number and note it. |
| **2.8 Total Operating Expenses** | All operating cost lines in aggregate, as reported on the face income statement in a subtotal labeled "Total costs and expenses," "Total operating expenses," or equivalent. **Verification:** Must equal `Row 2.1 + Row 2.4 + Row 2.5 + Row 2.6 + Row 2.7` plus any other separately disclosed operating cost lines present in the filing. If the sum of extracted components does not reconcile to the filing's stated total, check the face statement carefully for additional line items not captured (e.g., depreciation disclosed as a separate operating line, stock-based compensation presented separately). |

---

## Section 3 — Operating Results

| Line Item | Definition |
|---|---|
| **3.1 Operating Income (Loss)** | Income (or loss) from operations as reported on the **face of the Consolidated Statements of Income**, before interest, other non-operating items, and income taxes. Use the line labeled "Income from operations," "Operating income," "Operating profit (loss)," or equivalent. **Verification:** Must equal `Row 1.5 − Row 2.8`. Discrepancies indicate a missing operating cost line. **Sign:** Positive for income; negative for loss periods. |
| **3.2 Operating Margin (%)** | *(Calculated)* `Row 3.1 ÷ Row 1.5 × 100`. Round to one decimal place. Negative in loss periods. |

---

## Section 4 — Segment Operating Income

### Overview

This section disaggregates operating income by reportable segment. **Complete this section only if the filing discloses segment-level operating income (or equivalent "income from operations by segment").** If the filing does not disclose segment operating income, leave the entire section blank and add a note in the Analyst Notes section.

**Source for all rows in this section:** Notes to the Financial Statements — Segment Information note (annual figures). For quarterly figures, use the Segment Information note in the applicable quarterly report, or the segment profit table in the MD&A. Do not use the face income statement for this section.

---

| Line Item | Definition |
|---|---|
| **4.1 Segment OI — [1]** | Operating income (or loss) attributed to the first reportable segment, as reported in the Segment Information note. Use the same segment as row 1.1 and apply the same label. The segment's operating income definition follows the company's stated segment accounting policies, which may differ from consolidated GAAP operating income (e.g., stock-based compensation may be excluded from segment profit — record this in row 4.4). **Sign:** Positive for income; negative for loss. |
| **4.2 Segment OI — [2]** | Operating income (or loss) attributed to the second reportable segment. Apply the same sourcing and sign rules as row 4.1. |
| **4.3 Segment OI — [3]** | Operating income (or loss) attributed to the third reportable segment, or grouped "Other Segments" if more than three segments exist. Apply the same sourcing and sign rules as row 4.1. |
| **4.4 Unallocated / Corporate** | The reconciling amount between the sum of segment operating incomes and consolidated operating income as reported in the Segment Information note reconciliation table. Typically includes: corporate overhead not charged to segments, stock-based compensation excluded from segment profit, amortization of acquired intangibles not allocated to segments, restructuring charges held at corporate level, and other centralized costs. **Sign:** Typically negative (net cost burden on consolidated income). Enter as a signed number. **Cross-check:** `Row 4.1 + Row 4.2 + Row 4.3 + Row 4.4 = Row 3.1`. If this identity does not hold within ±1 unit of rounding, recheck all four values. |

---

## Section 5 — Non-Operating Income / Expense

### Overview

All items in this section are found below "Income from operations" and above "Income before income taxes" on the face income statement. Many companies aggregate these into a single "Other income (expense), net" line on the face statement and provide a component breakdown in a financial statement note. Where a note breakdown is available, **use the note** to populate rows 5.1–5.5 and use the face statement line for row 5.6.

---

| Line Item | Definition |
|---|---|
| **5.1 Interest Income** | Interest earned on cash, cash equivalents, short-term investments, and notes receivable. **Source:** Face income statement (if separately disclosed) or the "Other income (expense), net" note. **Inclusions:** All interest-type income regardless of balance sheet classification of the underlying instrument. **Sign:** Positive. |
| **5.2 Interest Expense** | Interest paid or accrued on long-term debt, bonds, revolving credit facilities, capital/finance leases, and similar obligations. **Source:** Face income statement or OI&E note. **Sign:** Enter as a **positive** number representing the magnitude of the expense. (Note: the filing may present this as a negative figure in the OI&E reconciliation; strip the sign and enter the absolute value here.) |
| **5.3 FX Gains (Losses), Net** | Net foreign currency transaction gains or losses recognized in the period, including realized and unrealized FX on monetary assets and liabilities, and gains or losses on FX derivative contracts not designated as hedges. **Exclusion:** Do not include revenue-hedging gains/losses already captured in row 1.4. **Sign:** Enter as a **signed number** — positive for a net gain, negative for a net loss. |
| **5.4 Gains (Losses) on Investments, Net** | Net realized and unrealized gains or losses on equity securities, debt investments, venture investments, and other financial assets held at fair value. **Inclusions:** Mark-to-market changes on equity securities under ASC 321 / IFRS 9, realized gains on sale of investments, and impairment charges on investments. **Note:** This line can be large and highly volatile for companies with significant investment portfolios (e.g., technology companies holding strategic equity stakes, insurance companies). **Sign:** Positive for net gain; negative for net loss. |
| **5.5 Other Non-Operating, Net** | All remaining non-operating items not captured in rows 5.1–5.4. **Inclusions:** Income or losses from equity method investments, LIFO liquidation gain adjustment (see rule 2.1), pension non-service costs, early debt extinguishment gains or losses, and other miscellaneous non-operating items. If a material item is included here, identify it in the Analyst Notes section. **Sign:** Signed number — positive for net income contribution, negative for net expense. |
| **5.6 Total Other Income (Expense), Net** | The total non-operating income or expense as reported on the **face of the Consolidated Statements of Income**. **Source:** Face statement line labeled "Other income (expense), net," "Non-operating income (expense)," or equivalent. **Verification:** Must reconcile to `Row 5.1 − Row 5.2 + Row 5.3 + Row 5.4 + Row 5.5`. If the reconciliation fails, check whether the OI&E note contains additional component lines not captured in rows 5.1–5.5. **Sign:** Positive when the net non-operating contribution is income; negative when it is a net expense. |

---

## Section 6 — Pre-Tax and Tax

| Line Item | Definition |
|---|---|
| **6.1 Income Before Income Taxes** | Pre-tax income or loss as reported on the face of the Consolidated Statements of Income. **Source:** Face statement line labeled "Income before income taxes," "Income (loss) before provision for income taxes," or equivalent. **Verification:** Must equal `Row 3.1 + Row 5.6`. Discrepancies indicate an error in either operating income or OI&E extraction. **Sign:** Positive for pre-tax income; negative for pre-tax loss. |
| **6.2 Provision for Income Taxes** | Total income tax expense or benefit for the period as reported on the face income statement. **Inclusions:** Current tax expense plus deferred tax expense (or benefit), as combined on the face statement. **Note:** For a full breakdown of current vs. deferred tax, refer to the Income Tax note; that detail is not captured in this template. **Sign:** Positive for tax expense (the normal case); negative for a net tax benefit period. |
| **6.3 Effective Tax Rate (%)** | *(Calculated)* `Row 6.2 ÷ Row 6.1 × 100`. Round to one decimal place. **Special cases:** (a) If pre-tax income is positive and the provision is negative (tax benefit on profitable year), the ETR is negative — enter the negative rate and note it. (b) If pre-tax income is negative (loss) and the provision is also negative (tax benefit), the ETR is positive — this is mathematically correct but may appear counterintuitive; note it. (c) Quarterly ETRs may differ significantly from the full-year ETR due to discrete tax items recognized in specific quarters; this is expected. |

---

## Section 7 — Net Income

| Line Item | Definition |
|---|---|
| **7.1 Net Income (Loss)** | The final bottom line of the Consolidated Statements of Income. **Selection rule:** If the filing presents both "Net income" and "Net income attributable to [Company] shareholders" (i.e., after deducting noncontrolling interests), use **"Net income attributable to [Company] shareholders"** as this is the figure relevant to common equity holders and consistent with the EPS denominator. If noncontrolling interests are immaterial or not present, the two figures will be identical. **Verification:** Must equal `Row 6.1 − Row 6.2`. **Sign:** Positive for net income; negative for net loss. |
| **7.2 Net Margin (%)** | *(Calculated)* `Row 7.1 ÷ Row 1.5 × 100`. Round to one decimal place. Negative in loss periods. |

---

## Section 8 — Per Share Data

All per share figures are extracted from the face of the Consolidated Statements of Income or the Earnings Per Share note. Enter values in the currency of reporting, rounded to **two decimal places**.

---

| Line Item | Definition |
|---|---|
| **8.1 EPS — Basic ($)** | Basic earnings (or loss) per share as reported in the EPS section of the income statement or EPS note. **Multi-class structures:** If the company has multiple share classes with different economic rights (e.g., tracking stocks, preferred stock participating in earnings), enter the basic EPS attributable to ordinary/common shareholders. If all classes carry equal economic rights, enter the single reported basic EPS. **Units:** Currency per share, two decimal places. |
| **8.2 EPS — Diluted ($)** | Diluted earnings (or loss) per share, reflecting the potential dilutive impact of outstanding options, warrants, RSUs, convertible instruments, and similar securities. **Anti-dilution rule:** In loss periods, dilutive securities are excluded from the denominator (since including them would reduce the loss per share, which is anti-dilutive). In a loss period, diluted EPS equals basic EPS — confirm this is consistent with the filing's reported figures. **Cross-check:** Diluted EPS ≤ Basic EPS in income periods; Diluted EPS = Basic EPS in loss periods. Flag any exceptions. **Units:** Currency per share, two decimal places. |
| **8.3 Wtd. Avg. Shares — Basic (MM)** | Weighted average number of common shares outstanding used to calculate basic EPS, expressed in millions. **Source:** Face income statement (below the EPS lines) or EPS note, labeled "Weighted-average shares outstanding — Basic" or "Number of shares used in per share calculation — Basic." Convert to millions if the filing reports in thousands or whole shares. **Units:** Millions of shares, rounded to the nearest whole million. |
| **8.4 Wtd. Avg. Shares — Diluted (MM)** | Weighted average diluted share count used to calculate diluted EPS, expressed in millions. Includes the dilutive effect of in-the-money options, unvested RSUs, and convertible instruments using the treasury stock method or if-converted method as applicable. **Cross-check:** Diluted shares ≥ Basic shares in income periods. In loss periods, Diluted shares = Basic shares (anti-dilution rule). Flag any exceptions. **Consistency check:** `Row 8.1 × Row 8.3 ≈ Row 7.1` and `Row 8.2 × Row 8.4 ≈ Row 7.1`, both within rounding tolerance. |

---

## Section 9 — Analyst Notes

Use this section to document:

| # | Note Type | What to Record |
|---|---|---|
| Standard | Segment assignments | Record the actual company segment names used for rows [1], [2], and [3] (e.g., "[1] = Cloud Services, [2] = Consumer Products, [3] = Other Ventures"). |
| Standard | Quarterly derivations | Note any quarterly figure computed rather than directly extracted (e.g., "Q4 2024 derived as FY 2024 minus Q3 YTD from 10-Q filing"). |
| Standard | Combined line items | Note any template rows left blank due to combined presentation (e.g., "SG&A not split; full amount entered in row 2.5"). |
| Standard | Absent line items | Note any rows left blank because the charge did not occur in the period or the metric is not applicable to this company. |
| As needed | Restatements | Note any prior-period figures that have been restated since originally filed, and the source of the restated figure. |
| As needed | Non-recurring items | Identify material non-recurring items embedded in the spread (e.g., "Row 2.7 includes $820M goodwill impairment charge in Q2 2023"). |
| As needed | Segment changes | Note any changes in reportable segments between periods (e.g., reorganizations, new segments added, segments discontinued or combined). |
| As needed | Currency | Note any significant currency effects or restatements from functional to presentation currency. |
| As needed | Non-GAAP departures | Note any instance where a GAAP figure was unavailable and a non-GAAP figure was used as a proxy. |

---

## Depreciation and Amortization — Supplemental Rules

These two line items appear in the template but are supplemental (marked "if not broken out"). The rules below govern when and how to populate them.

| Line Item | Definition |
|---|---|
| **D&A 1. Depreciation (Expense) [If not broken out]** | Enter the depreciation expense that is **included within Cost of Revenues (row 2.1)** and is not separately presented on the face income statement. **Purpose:** Allows reconstruction of cash-cost gross profit for comparability across companies that present depreciation differently. **Source:** Property, plant and equipment note or the cash flow statement reconciliation (where depreciation is disclosed as a non-cash add-back). **Only populate this row if** (a) depreciation is embedded in COGS and not separately broken out as a standalone income statement line, and (b) the filing provides a disclosure of the depreciation component embedded in COGS. If depreciation is presented as a separate line on the face income statement, enter it in the appropriate operating expense row and leave this supplemental row blank. **Sign:** Positive. |
| **D&A 2. Amortization (Expense) [If not broken out]** | Enter the amortization expense that is **included within Cost of Revenues (row 2.1)** and is not separately presented on the face income statement. Amortization of intangible assets acquired in business combinations (e.g., customer relationships, developed technology, trade names) is commonly embedded in COGS for technology and media companies. **Source:** Intangible assets note or the business combinations note, which typically discloses the amortization line allocated to COGS vs. operating expenses. **Only populate if** the company discloses the COGS-embedded component separately. **Sign:** Positive. |

---

## Cross-Check Summary

Upon completing the spread, verify all of the following identities for **every period column**. A failing check indicates a data entry error. Rounding tolerance is ±1 unit throughout.

| # | Check | Formula |
|---|---|---|
| X1 | Segment revenue reconciliation | Row 1.1 + 1.2 + 1.3 + 1.4 = Row 1.5 |
| X2 | Gross profit calculation | Row 1.5 − Row 2.1 = Row 2.2 |
| X3 | Operating income derivation | Row 1.5 − Row 2.8 = Row 3.1 |
| X4 | Segment OI reconciliation | Row 4.1 + 4.2 + 4.3 + 4.4 = Row 3.1 *(if section 4 is populated)* |
| X5 | OI&E component reconciliation | Row 5.1 − 5.2 + 5.3 + 5.4 + 5.5 = Row 5.6 |
| X6 | Pre-tax income derivation | Row 3.1 + Row 5.6 = Row 6.1 |
| X7 | Net income derivation | Row 6.1 − Row 6.2 = Row 7.1 |
| X8 | Diluted EPS ≤ Basic EPS | Row 8.2 ≤ Row 8.1 *(income periods only)* |
| X9 | Diluted shares ≥ Basic shares | Row 8.4 ≥ Row 8.3 *(income periods only)* |
| X10 | Basic EPS consistency | Row 8.1 × Row 8.3 ≈ Row 7.1 *(within rounding tolerance)* |

---

*End of Manual — Version 1.0*

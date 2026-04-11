# Spreading Manual — Income Statement
**Version 2.1 | Credit Analysis Division**
*For internal use by underwriting and portfolio management staff.*

---

## Purpose

This manual establishes uniform standards for spreading income statement data from company filings into the Income Statement Spread template. Consistent spreading is essential for peer comparisons, trend analysis, and credit model inputs. Analysts should treat this manual as authoritative; judgment calls should be documented in the Analyst Notes section of the spread.

---

## 1. Before You Begin

### 1.1 Selecting the Source Document

Always spread from the company's own SEC filing (10-K for annual periods, 10-Q for interim quarters), not from third-party data aggregators. Aggregators restate and reclassify items according to their own taxonomies, which introduces inconsistencies across our portfolio.

For public companies, retrieve filings directly from SEC EDGAR. For foreign private issuers reporting on Form 20-F or 6-K, apply the same principles but note that fiscal year conventions and presentation formats will differ.

### 1.2 Identifying the Correct Financial Statement

Use the **Consolidated Statements of Income** (sometimes titled "Consolidated Statements of Operations" or "Consolidated Statements of Earnings"). Do not spread from summary tables in the MD&A, earnings releases, or investor presentations — these are not audited and companies sometimes adjust line items for presentation.

### 1.3 Choosing the Right Columns

SEC filings present multiple periods side by side. For a 10-Q, the statement typically shows:

- The current quarter ("Three Months Ended...")
- The same quarter one year prior

It may also show year-to-date figures. Spread **quarterly columns only** into the quarterly cells of the template; do not blend quarterly and year-to-date figures. Annual totals in the template should either come from the 10-K or be summed from the four quarterly spreads — note which approach was used.

### 1.4 Units and Currency

Read the parenthetical beneath the statement title, e.g., *(in millions, except per share amounts)*. Enter values in those units. Record the unit in the template header. If a company reports in thousands, divide by 1,000 before entering, and note the conversion.

All values should be in USD. If the company reports in a foreign currency, record the reporting currency in the template header and do not convert; flag for the deal team to apply FX adjustments separately.

---

## 2. Revenue

### 2.1 Segment Revenue Rows

Most large companies disaggregate revenue by business segment, product line, or geography, either on the face of the income statement or in a revenue footnote. Enter each major segment as a labeled row. Use the segment names exactly as the company uses them — do not rename or abbreviate. Record the segment names in the Analyst Notes.

If a company does not report segment revenue, leave those rows blank and spread only the Total Revenues line.

### 2.2 Other / Eliminations

Use this row for inter-segment eliminations, hedging program gains and losses reported as a revenue reconciling item, or any other company-specific revenue line that does not fit a named segment. Describe the item in Analyst Notes.

### 2.3 Total Revenues

Always verify that the segment rows you have entered sum to Total Revenues. A difference of more than $1MM (after accounting for rounding) indicates a segment you have missed or an error in source selection. Investigate before proceeding.

### 2.4 YoY Growth

Calculate as *(Current Period − Prior Period) / Prior Period × 100*. Leave blank for the earliest period in the spread where no prior-year comparison is available.

---

## 3. Costs and Expenses

### 3.1 Cost of Revenues

Enter the line labeled "Cost of revenues," "Cost of sales," or "Cost of goods sold" exactly as reported. Do not net this against revenues. For companies where cost of revenues is not separately broken out (rare among public companies), leave blank and note it.

**Gross Profit** and **Gross Margin** are calculated rows — do not enter them manually. Gross Profit = Total Revenues − Cost of Revenues; Gross Margin = Gross Profit ÷ Total Revenues.

### 3.2 Operating Expense Lines

Enter Research and Development, Sales and Marketing, and General and Administrative as reported on the face of the statement. Some companies use different labels:

- "Product development" is equivalent to Research and Development.
- "Marketing and sales" or "Selling, general and administrative (SG&A)" — if a company presents a single SG&A line rather than splitting Sales and Marketing from G&A, enter the combined figure in General and Administrative, leave Sales and Marketing blank, and note the presentation in Analyst Notes.

### 3.3 Restructuring and Other Charges

Enter charges labeled "Restructuring," "Impairment," "Severance," or similar as a separate line. These are non-recurring items that distort trend analysis if buried in functional expense lines. If the company embeds restructuring within functional lines and discloses the amount only in a footnote, enter zero here but note the embedded amount in Analyst Notes.

### 3.4 Stock-Based Compensation

Do not create a separate row for stock-based compensation (SBC). It is already included within the functional expense lines (Cost of Revenues, R&D, Sales and Marketing, G&A). Companies typically disclose SBC by functional category in a footnote; record the total SBC figure in Analyst Notes for reference, as it is relevant to adjusted EBITDA calculations.

### 3.5 Total Operating Expenses

This is the sum of all expense lines above. Verify: Total Revenues − Total Operating Expenses must equal Operating Income as reported on the face of the statement. If it does not, there is a missing line item.

---

## 4. Operating Results

### 4.1 Operating Income (Loss)

Enter directly from the face of the income statement. Do not calculate. If your sum of revenues and expenses does not reconcile to the reported figure, identify the discrepancy before proceeding — do not override the reported figure.

### 4.2 Operating Margin

Calculated as Operating Income ÷ Total Revenues × 100. Negative margins are common for early-stage or loss-making companies; enter them as negative percentages.

---

## 5. Segment Operating Income

Complete this section only if the company discloses segment profitability in its footnotes. Many companies disclose segment revenues but not segment operating income; in that case, leave this section blank.

Segment operating income figures typically appear in a "Segment Information" footnote, not on the face of the income statement. The sum of all segment operating income rows plus the Unallocated / Corporate row must equal consolidated Operating Income. A reconciling "Unallocated / Corporate" line, representing shared costs and items not allocated to segments, is common and is usually negative.

If the company changes its segment structure during the spread period, use the restated prior-period figures the company provides for comparability, and note the change.

---

## 6. Non-Operating Income / Expense

These items appear in a section the company typically labels "Other income (expense), net" or "Non-operating income (expense)" on the face of the statement. Many companies provide a footnote breakdown; use the footnote to populate the individual lines and use the face-of-statement total as your reconciliation anchor.

### 6.1 Interest Income

Interest earned on cash and short-term investments. Always positive.

### 6.2 Interest Expense

Interest on debt obligations. Enter as a **negative** value. If the company presents it in parentheses on the statement, it is already indicating a negative; enter as a negative number in the template.

### 6.3 FX Gains (Losses), Net

Transaction-based foreign currency gains and losses on operating balances. Can be positive or negative quarter to quarter. Do not confuse with revenue-line hedging adjustments, which belong in the Other / Eliminations revenue row.

### 6.4 Gains (Losses) on Investments, Net

Unrealized and realized gains and losses on equity securities, venture investments, and other financial instruments. This line is often large and volatile for companies with significant investment portfolios. Note particularly large swings in Analyst Notes, as they can dominate reported net income and obscure operating performance.

### 6.5 Other Non-Operating, Net

Any remaining non-operating items not captured above. If multiple small items are lumped here, list them in Analyst Notes.

### 6.6 Total Other Income (Expense), Net

Must equal the face-of-statement total for this caption. If your component lines do not sum to it, there is a missing item in the footnote breakdown.

---

## 7. Pre-Tax Income and Taxes

### 7.1 Income Before Income Taxes

Enter directly from the face of the statement. Cross-check: Operating Income + Total Other Income (Expense) must equal this figure.

### 7.2 Provision for Income Taxes

Enter as a **positive** number representing a tax expense. A tax benefit (negative provision) should be entered as a negative number; note it explicitly in Analyst Notes, as a tax benefit can significantly inflate reported net income and may not recur.

### 7.3 Effective Tax Rate

Calculated as Provision for Income Taxes ÷ Income Before Income Taxes × 100. Leave blank if Income Before Income Taxes is zero or negative, as the ratio is not meaningful. Effective tax rates that are unusually low or high relative to the statutory rate warrant a note.

---

## 8. Net Income

### 8.1 Net Income (Loss)

Enter from the face of the statement. Cross-check: Income Before Income Taxes − Provision for Income Taxes = Net Income.

For companies with noncontrolling interests, the income statement will show both "Net income" and "Net income attributable to [Company]." Use **Net income attributable to the parent company** — this is what flows to EPS.

### 8.2 Net Margin

Calculated as Net Income ÷ Total Revenues × 100.

---

## 9. Per Share Data

### 9.1 Basic and Diluted EPS

Enter exactly as reported. Diluted EPS must be less than or equal to Basic EPS; if it appears higher, the difference is due to anti-dilutive securities being excluded — note this. EPS figures are in **dollars per share**, not millions.

### 9.2 Weighted Average Shares

Enter in **millions of shares**. Confirm the units in the filing's parenthetical — some companies report shares in thousands, requiring a divide-by-1,000 conversion. Diluted share count must be equal to or greater than Basic share count.

Cross-check: Net Income ÷ Basic Weighted Average Shares should approximate Basic EPS (within $0.02, given rounding in the filing).

---

## 10. Special Situations

### 10.1 Restatements and Reclassifications

If a filing includes restated prior-period figures, use the restated figures and note the nature and magnitude of the restatement. Do not mix restated and as-originally-reported figures across periods in the same spread.

### 10.2 Discontinued Operations

If the income statement includes a "Income (loss) from discontinued operations" line below Net Income from continuing operations, enter it in Other Non-Operating, Net with a clear note. Do not net it into operating line items. When a discontinued operation is large, consider spreading continuing and discontinued operations separately and flagging for the deal team.

### 10.3 Non-Recurring Items

Items such as litigation settlements, gain on sale of a business, impairment of goodwill, or insurance recoveries should be entered in the most appropriate line (often G&A or Restructuring). Always note them individually, as they affect normalized earnings analysis.

### 10.4 Changes in Accounting Policy

A change in accounting principle (e.g., adoption of a new revenue recognition standard) may recast prior periods or create a cumulative adjustment. Note any such change and confirm that the periods being spread are on a comparable basis. If they are not, flag the break in comparability prominently.

### 10.5 52/53-Week Fiscal Years

Retailers and certain other companies close their fiscal year on a day of the week (e.g., the Saturday nearest January 31) rather than a calendar month-end. This means one fiscal year in every five or six will contain 53 weeks. When present, note the extra week and its estimated revenue impact if disclosed by management.

---

## 11. Common Errors to Avoid

- **Spreading from earnings releases or investor presentations** rather than the SEC filing. These sources reclassify items and exclude charges that appear in the GAAP filing.
- **Double-counting segment revenues** by entering both a segment subtotal and its component lines.
- **Mixing quarterly and year-to-date columns.** A nine-month figure in a quarterly column will corrupt all ratio calculations.
- **Entering EPS in millions.** EPS is dollars per share; shares outstanding is in millions. These belong in different rows and must not be confused.
- **Treating a large investment gain as recurring operating income.** Unrealized gains on equity portfolios are non-cash, highly volatile, and not indicative of operating performance. Always note them.
- **Omitting the Unallocated / Corporate line** when spreading segment operating income, causing the segment OI total to exceed consolidated OI.

---

*Questions regarding the application of this manual to specific situations should be directed to the Credit Analysis Group.*

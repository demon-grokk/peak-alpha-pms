# High-Alpha PMS — Project Requirements & Vision

## 0. Purpose of This Document

This is the source-of-truth brief for Antigravity (dev agent) to build this project. It defines what
we are building, why, in what order, and with what constraints. Every task handed to the agent
should trace back to a section here. When in doubt, the agent should favor the constraints in
Section 2 (zero-budget, personal-use, verifiable-in-small-steps) over adding sophistication.

This is a **personal tool for personal capital**. It is not a product, not a SaaS, not sold or
distributed to others, and it does not publish recommendations to third parties. Keep that framing
in every design decision — it directly justifies why we skip infrastructure other "screener SaaS"
architectures would need (multi-tenancy, auth, uptime SLAs, compliance registration, etc.).

---

## 1. Vision — What We Are Ultimately Building

A **personal high-alpha portfolio management system (PMS)**, built in four layers that get built in
this order but should be designed with all four in mind from day one:

1. **Discovery** — quantitative screener(s) that surface candidate stocks from the full market.
2. **Classification** — tiering of stocks by conviction/performance (Super-Performer, Performer,
   Hold, Under-Performer, Exit — mirroring a conviction ladder, not just binary in/out).
3. **Allocation** — capital weighting rules across tiers and individual positions.
4. **Governance** — entry/exit principles, red-flag checks, and review cadence discipline.

The long-term reference UI is a performance-breakdown dashboard: tiered buckets with per-stock
allocation %, alpha vs. benchmark, rank movement, a portfolio allocation donut chart, and an
overall alpha figure vs. a benchmark index. We are not building this dashboard now — we are
building the data layer and discovery engine it will eventually sit on top of. Do not build UI
before the underlying data pipeline is verified and trustworthy.

**Guiding principle for every phase:** validate cheaply before you build expensively. Every layer
should be provable with a CSV and a manual sanity-check before it becomes a service, a database
migration, or a UI component.

---

## 2. Hard Constraints (Non-Negotiable for MVP)

- **Zero budget.** No paid APIs, no paid data vendors, no paid hosting. Free/official sources only
  (NSE/niftyindices.com official CSVs, `yfinance`, Screener.in's own export/watchlist features
  used as intended — not scraped in a way that violates their terms).
- **Single user, no concurrency, no uptime requirement.** This runs on a personal machine, on
  demand or via a simple local scheduler. Do not build for multi-user access, load balancing, or
  high availability. SQLite over Postgres/TimescaleDB. Plain pandas over Polars/DuckDB. No
  Airflow/Prefect — a simple script + local cron/Task Scheduler is enough.
- **Task-based delivery.** Every unit of work given to the agent must be independently checkable
  against a small number of known stocks (e.g., verify Reliance/TCS/HDFC Bank numbers by hand)
  before the next task starts. No single mega-build.
- **Not a public product.** No auth system, no multi-tenant data model, no SEBI Research Analyst
  registration needed — this only becomes relevant if/when recommendations are ever published to
  others, which is explicitly out of scope for now.
- **Scale only after validation.** Universe size, benchmark strictness, automation depth, and infra
  sophistication all expand in stages, only after the previous stage has produced output the user
  trusts.

---

## 3. Phase 1 Scope (What Gets Built Right Now)

### 3.1 Universe

- Source: **Nifty Total Market Index** (NOT "Nifty 750" — that is informal shorthand). This index
  is Nifty 500 + Nifty Microcap 250, ~750–752 constituents, rebalanced semi-annually (effective
  last trading day of March and September).
- Official constituent file: `https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv`
  — confirmed live, returns Company Name, Industry, Symbol, Series, ISIN Code for all ~750 rows.
- Treat the exact count as a moving target (750–752+), not a hardcoded constant.
- Each stock in this file has a sector/industry tag **and an ISIN** — capture and store both now
  even though the sector-index benchmark leg (Section 3.2, Filter 3 Stage B) and the ISIN-based
  universe join (Section 6, Step 2) are both deferred. Storing them from day one avoids a
  disruptive schema change later — see Section 7.

### 3.2 Entry Filter — The 3-Point Criteria

A stock qualifies only if it passes all applicable filters for the current phase. This is the
discovery layer's current sole strategy (future strategies described in Section 5 are explicitly
out of scope for Phase 1).

**Filter 1 — All-Time High Price**
- Use **split/bonus-adjusted close** only. Never use raw/unadjusted close — a stock that has
  split will falsely and permanently fail an unadjusted-price ATH check. `yfinance` provides
  adjusted close automatically.
- Logic: `current_adj_close >= max(adj_close_history_since_listing)`. No arbitrary tolerance band
  — exact new-high logic, since the entire point of the filter is "no overhead resistance."
  Implementation note: use a tiny floating-point epsilon (e.g., `1e-6`) for the equality check,
  not a percentage-based "near high" band. Split-adjustment math involves repeated float
  multiplication, so a genuine tie can occasionally miss exact equality by a rounding hair — the
  epsilon is a numerical-safety fix only, it does not loosen the filter's actual logic.
- **Two-Tier Eligibility Structure**:
  1. **Primary List (`is_ath_price = True`, $\ge$ 756 trading days / 3 years)**: Core, trusted qualifying list for `passes_all_filters`. Protects against thin-data false positives.
  2. **Emerging-Momentum Watchlist (`is_emerging_candidate = True`, 252–755 trading days / 1 to 3 years)**: Flags stocks at ATH with 1 to 3 years of history for separate manual verification.
  3. **Excluded (< 252 trading days / 1 year)**: Stocks with fewer than 252 trading days are excluded from both tiers.

**Filter 2 — All-Time High TTM Profit (ex-exceptional items)**
- Definition: trailing-twelve-month PAT, normalized to exclude one-time/exceptional items, must
  be at its own all-time high across all available quarterly history.
- This filter runs on a **different clock than Filters 1 and 3** — see Section 3.3, this is a core
  architectural decision, not an implementation detail.
- Resolution order per stock (cheapest/most reliable first):
  1. Check Screener.in quarterly data for an explicit "Exceptional items" or "Other income"
     line. If present, subtract it directly from reported PAT. No heuristic needed for this case.
  2. If no explicit line exists, fall back to a **two-signal heuristic**: (a) PAT growth YoY is
     far out of proportion to revenue/EBITDA growth YoY, AND (b) profit is at/near its ATH while
     revenue is not anywhere close to its own historical high. Both signals agreeing is the flag
     condition — a single signal alone is not enough to auto-exclude, only enough to flag for
     manual review.
  3. Anything flagged gets a manual look, batched (see 3.4).
- Bias the heuristic **loose, not tight**. The two failure modes are not symmetric: over-flagging
  costs a few minutes of review; under-flagging silently lets a land-sale-inflated stock pass,
  which defeats the filter's purpose entirely. Start generous, log the outcome of every manually
  reviewed flag (real exceptional item vs. false alarm), and tighten the heuristic later using
  that logged data — do not hand-guess a final threshold on day one.
- Special handling: banks/NBFCs/insurers use "Net Profit" as reported rather than standard PAT
  treatment — tag these with an `is_financial_sector` flag and branch logic accordingly.
- If a stock's all-time-max TTM PAT is negative or ~zero, it cannot pass this filter — skip it
  rather than treating a "high" loss as an all-time high.

**Filter 3 — Relative Outperformance (52-week) — PHASED ROLLOUT**

This filter itself is being rolled out in two stages. Build only Stage A now; Stage B is a
Phase 2 addition, not a Phase 1 task.

- **Stage A (Phase 1 — build now):** Stock's 52-week (252 trading day) adjusted-price return must
  exceed the **BSE-500** index's 52-week return. BSE-500 is used because it requires no per-stock
  sector-mapping table, keeping Phase 1 fully automatable with a single benchmark series. Note:
  comparing a stock's relative strength against a broad index it happens to be a constituent of is
  standard practice (e.g., IBD's RS Rating methodology works this way against the S&P 500) — the
  choice of BSE-500 isn't about avoiding overlap with the universe, it's purely about deferring the
  sector-mapping dependency to Phase 2.
- **Stage B (Phase 2 — build later, after Stage A is validated):** Add a second condition — stock's
  52-week return must *also* exceed its mapped NSE sector index's 52-week return. This requires
  building the manual stock → sector-index mapping table (NSE publishes 30+ sectoral/thematic
  indices; there is no clean automated 1:1 mapping API, so this table is hand-built once and
  refreshed at each semi-annual universe rebalance). Do not build this mapping table or the
  sector-index leg until Stage A has run and the resulting shortlist has been reviewed and trusted.
- Once Stage B ships, Filter 3 reverts to the originally specified logic: outperform **both** BSE
  500 (or its Phase 2 equivalent) **and** the sector index, not either/or.

### 3.3 The Two-Clock Architecture (Critical Design Decision)

Do not treat this as one weekly batch job. Filters 1 and 3 depend on price data that changes every
trading day; Filter 2 depends on fundamental data that only changes when a specific company
reports its quarterly results (roughly 4x/year, staggered across a ~6-week results season per
quarter). Build two independent, decoupled processes:

- **Price process (calendar-triggered):** runs on a simple schedule (daily or weekly, user's
  choice) and recomputes Filters 1 + 3 for the full universe. Fully automated, no manual load.
- **Fundamentals process (event-triggered):** maintains a `last_verified_ttm_pat` cached value
  and a `last_result_date` per stock. On each run, only re-evaluates Filter 2 for stocks that have
  a newer quarterly result posted since their last check. The other ~95% of stocks in a typical
  week just carry forward their last verified pass/fail state.

This decoupling — not the heuristic threshold — is what keeps the manual-review pile small on an
ongoing basis. Most weeks only a handful of shortlisted names will have a new result to check at
all.

### 3.4 Manual Review Funnel (Filter 2 Fallback Path)

For any stock that reaches step 2 or 3 of the Filter 2 resolution order (Section 3.2):

1. Batch all stocks needing manual review into a single Screener.in **watchlist**, then use the
   watchlist's built-in export feature to get one spreadsheet covering all of them. Do not visit
   company pages one by one, and do not scrape — this is a sanctioned, intended use of the site.
2. Manually confirm/reject exceptional-item adjustment for that batch only.
3. Log the outcome (confirmed exceptional item vs. false alarm from the heuristic) — this log is
   what future-tunes the heuristic thresholds in Section 3.2.

### 3.5 Phase 1 Output

A single reproducible output (CSV, plus SQLite as the underlying store) listing every stock that
passes Filter 1 + Filter 2 + Filter 3 Stage A, with supporting columns: current adjusted price,
ATH price, current TTM PAT (normalized), ATH TTM PAT, 52w stock return, 52w BSE-500 return, sector
tag and ISIN (stored but not yet used as filter conditions), and `last_verified_ttm_pat` date. No
UI required for Phase 1 — a CSV/table view is sufficient to validate the strategy produces a
trustworthy list.

---

## 4. Technical Stack (Phase 1)

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python | Best-supported ecosystem for market data at zero cost |
| Universe source | NSE/niftyindices official CSV | Free, official, authoritative; confirmed live |
| Price data | `yfinance` (adjusted close, batched via `yf.download()`) | Free, adjusted-close built in |
| Benchmark (Phase 1) | BSE-500 index, via `yfinance` | Free, no sector-mapping dependency |
| Fundamentals (coarse) | `yfinance` quarterly financials, where available | Free; known to be patchy for smaller caps — treat missing data as "needs manual review," never as a silent fail |
| Fundamentals (precise) | Screener.in watchlist export | Free, sanctioned, batchable |
| Storage | SQLite | Single-user, no concurrency needed |
| Compute | pandas | Data volume (≤~800 stocks × years of daily data) does not justify Polars/DuckDB at this stage |
| Scheduling | Manual run or local cron/Task Scheduler | No need for Airflow/Prefect at single-user scale |
| Output | CSV / simple table | No frontend until the filter output is proven useful |

Do not introduce TimescaleDB, ClickHouse, Postgres, FastAPI, React, Docker, or cloud hosting in
Phase 1. These are justified only if/when this scales beyond personal use (see Section 6).

---

## 5. Future Vision (Scaffold For, Do Not Build Yet)

The agent should keep the data model (Section 7) flexible enough to accommodate these without
schema rewrites, but none of the following are Phase 1 tasks.

### 5.1 Filter 3 Stage B — Sector Index Benchmark
Described in Section 3.2. Build the stock → sector-index mapping table and add the sector-index
outperformance leg only after Stage A (BSE-500.BO only) has run and been validated against manual
sanity-checks over at least one review cycle.

### 5.2 Additional Selection Strategies
The 3-point entry filter is one strategy, not the only one. Future strategies should attach to
stocks as a `strategy_tag` rather than requiring separate tables — for example a value/mean-
reversion strategy, a turnaround (loss-to-profit) strategy, or a sector-rotation strategy.

### 5.3 Exit Strategies
Exit is not binary; it mirrors a conviction ladder (Super-Performer → Performer → Hold →
Under-Performer → Exit). Candidate rules to formalize later:
- Momentum exit: price falls meaningfully below its ATH zone and stays there past a defined window.
- Fundamental exit: TTM PAT rolls over from its ATH for two consecutive quarters.
- Relative-strength exit: stock underperforms its sector index or benchmark over a trailing window
  (e.g., 13 weeks).
- Alpha-decay exit: rolling alpha (position return minus benchmark return — simple alpha is
  sufficient for this use case, no need for CAPM-beta-adjusted/Jensen's alpha) turns and stays
  negative.

### 5.4 Allocation Rules
Percentage allocation by conviction tier (mirroring the target dashboard's tier-weighted donut
chart). To be defined as configurable parameters, not hardcoded values: max % per single position,
target % per tier, minimum cash buffer.

### 5.5 Buying Principles (Qualitative Pre-Filter)
A checklist layer ahead of the quantitative filters: minimum promoter holding, no rising pledged-
share trend, no qualified audit opinions, no recent auditor resignation, no active regulatory
investigation. Starts as a manual checklist; fields should exist in the schema (`governance_flag`,
`promoter_holding_pct`, `pledge_pct`) so it can be automated later as data sources allow.

### 5.6 Governance & Legal
- This remains a personal tool for personal capital — no SEBI Research Analyst registration is
  required unless/until recommendations are ever published or shared with others as advice.
- Track governance red flags per stock (related-party transactions, pledge trend, auditor changes)
  as boolean/enum fields even before they're populated by real data.

### 5.7 Dashboard (Reference UI, Not Phase 1)
Eventual UI mirrors a performance-breakdown view: tiered buckets (Super-Perf/Performers/Hold/
Under-Perf/Exit/Others) each showing allocation %, return %, alpha vs. benchmark, and rank
movement; a portfolio allocation donut chart by tier; and an overall alpha figure vs. a stated
benchmark (e.g., MTD/period return comparison). Build only after Phases 1–2 produce a trustworthy,
ongoing stock list and a real (even if small/paper) portfolio to track.

---

## 6. Scaling Path (Trigger-Based, Not Time-Based)

Each step below is justified only by evidence from the previous step, not by a calendar date.

1. **Filter 3 Stage B (sector index benchmark)**: the first scaling step, ahead of universe
   expansion — see Section 5.1.
2. **Universe expansion beyond Nifty Total Market**: only if the strategy proves useful and more
   candidates are wanted. Next step is the SEBI/AMFI semi-annual stock categorization list (all
   listed companies ranked by 6-month average market cap), joined to NSE's ISIN→symbol master
   using the ISIN already captured per Section 3.1 — a lookup join, not a schema change. At that
   point, add a liquidity pre-filter (minimum average daily traded value, e.g., ₹1 crore+ over
   20–60 days) and exclude T2T-surveillance-flagged stocks, since illiquid names produce false
   ATH signals.
3. **Paid fundamentals vendor** (e.g., Tijori): only if the manual Screener.in review step becomes
   an actual bottleneck at higher volume, not preemptively.
4. **Real-time or broker-API price data** (e.g., Zerodha Kite Connect): only if EOD daily data
   becomes insufficient for the strategy's actual needs.
5. **Heavier infra** (Postgres/TimescaleDB, scheduler frameworks, a real frontend): only once a
   working portfolio-tracking layer (Section 5.7) exists and is used regularly enough that manual
   CSV review is itself the bottleneck.

---

## 7. Data Model Guidance (Design Now, Populate Incrementally)

Structure tables so Phase 1 needs are met and Section 5 features are additive, not disruptive:

- `stocks`: symbol, isin, name, sector, sector_index_mapping (nullable until Stage B),
  listing_date, is_financial_sector, strategy_tags (extensible list/field)
- `price_history`: symbol, date, adj_close, volume (daily, since listing)
- `benchmark_history`: benchmark_name (e.g., BSE500, sector indices later), date, adj_close
- `fundamentals_quarterly`: symbol, quarter_end, reported_pat, exceptional_items (nullable),
  pat_normalized, revenue, ebitda, last_result_date
- `fundamentals_ttm`: symbol, as_of_quarter, ttm_pat_normalized, is_ath_flag,
  last_verified_ttm_pat, needs_manual_review (bool), review_outcome (nullable, for heuristic tuning)
- `screen_results`: symbol, run_date, is_ath_price, is_ath_profit, outperforms_bse500,
  outperforms_sector (nullable until Stage B), passes_all_filters
- `governance_flags` (schema only for now): symbol, promoter_holding_pct, pledge_pct,
  governance_flag, notes
- `portfolio_positions` (schema only for now): symbol, tier, allocation_pct, entry_date,
  entry_price, current_alpha, rank

`isin` is included in `stocks` from Phase 1 (sourced directly from the Nifty Total Market
constituent CSV, which already provides it) specifically so the Section 6, Step 2 universe-
expansion join against NSE's ISIN→symbol master requires no backfill later.

---

## 8. Task Sequencing for Antigravity

Each task must be completed and manually verified against 3–5 known stocks before the next
begins. Do not combine tasks. Do not build ahead of the current task's verification.

1. **Universe + price pipeline**: fetch `ind_niftytotalmarket_list.csv` (capture symbol, name,
   sector, and ISIN per row), batch-download adjusted daily OHLC via `yfinance` for all
   constituents, store in SQLite. Verify against a known stock's real historical ATH.
2. **Filter 1 + Filter 3 Stage A engine**: ATH-price detector (exact equality with `1e-6`
   epsilon, 756-trading-day/3-year minimum history exclusion) and 52-week relative-outperformance
   calculator vs. BSE-500 only (no sector mapping table yet). Verify against 2–3 stocks expected
   to pass and 2–3 expected to fail.
3. **Filter 2 coarse pass + manual review funnel**: `yfinance` quarterly financials pull, explicit
   exceptional-items check, two-signal heuristic flagging, Screener.in watchlist export step for
   the flagged batch. Verify the heuristic's flags against a handful of manually-checked names.
4. **Two-clock scheduling**: separate calendar-triggered (price) and event-triggered
   (fundamentals) processes, with `last_verified_ttm_pat` state persisted between runs.
5. **Combined output**: merge Filter 1 + Filter 2 + Filter 3 Stage A into one query/report,
   output final CSV.
6. **(Phase 2, do not start yet) Filter 3 Stage B**: build the sector-index mapping table and add
   the sector-outperformance condition, only after Task 5's output has been reviewed and trusted
   over at least one full cycle.

Do not proceed to Section 5 (future vision) items or Task 6 until Section 3 (Phase 1) is fully
verified and producing a stock list the user trusts through at least one full review cycle.

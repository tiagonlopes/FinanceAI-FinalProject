# Stock Analyzer — Project Guide

## Purpose

Pre-market intelligence tool for a swing trader focused on **directional options (buying calls/puts)** with 2–5 day hold periods. The goal is to arrive at market open with a clear, structured view of every position and watchlist candidate — before the first trade.

The 5-day forecast window in `src/technical.py` is intentionally matched to this hold period.

---

## How to Run

```bash
# Python runtime is the "finance_ai_agent" conda env (anaconda3) — system python3 is too old
cd "/Users/tiagolopes/Library/CloudStorage/OneDrive-Personal/Courses/Finanzas AI/FinalProject"

# Full run (fetch + analyze + report + critique)
~/anaconda3/envs/finance_ai_agent/bin/python3.11 main.py --tickers AAPL MSFT NVDA TSLA

# Regenerate reports from cached data (no network calls)
~/anaconda3/envs/finance_ai_agent/bin/python3.11 main.py --tickers AAPL --skip-fetch

# Pipeline only — skip Claude API (useful for debugging data)
~/anaconda3/envs/finance_ai_agent/bin/python3.11 main.py --tickers AAPL --no-report -v

# Skip the critique agent (faster, just main report)
~/anaconda3/envs/finance_ai_agent/bin/python3.11 main.py --tickers AAPL --no-critique

# Use the core watchlist (watchlist.txt) plus rotating adds for the week
~/anaconda3/envs/finance_ai_agent/bin/python3.11 main.py --watchlist --tickers PLTR SOFI

# Use a different watchlist file
~/anaconda3/envs/finance_ai_agent/bin/python3.11 main.py --watchlist core_swing.txt
```

Alternatively, activate the env first: `conda activate finance_ai_agent` then run `python main.py ...` directly.

SPY, QQQ, DIA, and IWM are **always appended automatically** as market benchmarks — do not add them manually.

---

## Watchlist Design

The user has a **hybrid watchlist**: a fixed core of high-conviction stocks that are always analyzed, plus rotating adds based on what's in play that week.

The core list lives in `watchlist.txt` (one ticker per line, `#` for comments, gitignored-friendly). Use `--watchlist` to load it:

```bash
# Core list only
python main.py --watchlist

# Core list + rotating adds for the week
python main.py --watchlist --tickers PLTR SOFI

# A different watchlist file
python main.py --watchlist core_swing.txt
```

`--tickers` and `--watchlist` are combined and de-duplicated. At least one of them must be provided.

---

## Trading Context (Critical for Analysis)

Understanding this context shapes how the reports should be written and what signals matter most:

**Strategy:** Directional options — buying calls in uptrends, buying puts in downtrends. Not selling premium, not running spreads. Leverage is intentional.

**What this means for the analysis:**
- **Trend and momentum are the primary signal.** RSI, MACD, ADX, and SMA stack alignment matter more than valuation multiples.
- **IV rank matters critically.** Buying options when implied volatility is historically high = overpaying for leverage. The current app does not yet track IV rank — this is the top missing feature.
- **Earnings dates are a hard constraint.** Holding a directional options position through an earnings announcement is a binary event that can destroy the trade. Earnings date awareness needs to be built in.
- **The 5-day forecast window is intentional.** ARIMA + Prophet forecasts are calibrated for 2–5 day holds. Forecast agreement ("converging" vs "diverging") is a direct signal for or against entering a position.
- **Fundamental analysis is secondary.** P/E ratios and balance sheet metrics matter less for short-swing directional plays. Fundamentals are useful as a quality filter and for understanding why a stock is moving, not for timing entry.

**The two most important questions before entering a directional options trade:**
1. Is there a clear trend with momentum confirmation? (technical)
2. Is there an earnings announcement inside my intended hold window? (catalyst risk)

---

## Architecture Overview

```
main.py              CLI entry point — argparse, asyncio.run
config.py            Constants: benchmarks, model, token limits
src/
  fetcher.py         yfinance data fetch — asyncio.Semaphore(3) + tenacity retry
  technical.py       Indicators (ta lib) + ARIMA(5,1,0) + Prophet dual forecast
  fundamental.py     Fundamentals + sector comparison; ETF branch → BenchmarkFundamentals
  runner.py          asyncio.gather + ThreadPoolExecutor orchestrator
  reporter.py        Claude version of ReportingAgent/CritiqueAgent, claude-opus-4-8 (not used by main.py anymore)
  reporter_gemini.py Active reporter — ReportingAgent (blue HTML) + CritiqueAgent (red HTML), gemini-2.5-flash
  utils.py           StockJSONEncoder, logging, save/load_intermediate
data/                Intermediate JSON cache (gitignored)
reports/             Output HTML + MD (gitignored)
```

**Active LLM provider: Gemini.** `main.py` imports `ReportingAgent`/`CritiqueAgent` from `src/reporter_gemini.py`, which hardcodes `_MODEL = "gemini-2.5-flash"`. It reuses the system prompts (`_REPORT_SYSTEM`, `_CRITIQUE_SYSTEM`), data-section builder, and HTML wrapper from `src/reporter.py` — only the API client and streaming call differ (`google-genai` SDK vs `anthropic` SDK). Output files are suffixed `_gemini` (`report_gemini.md/html`, `critique_gemini.md/html`) to distinguish from the legacy Claude outputs (`report.md/html`, `critique.md/html`).

**Each run overwrites the previous report/critique** — filenames are fixed (no timestamp), so `reports/` always holds just the latest output. This keeps the directory clean ahead of the planned migration to an API + web frontend, where the backend will serve the single latest report rather than a list of files.

**Why gemini-2.5-flash, not gemini-2.5-pro:** the configured `GEMINI_API_KEY` is on the free tier, which has a **0 request/token quota for `gemini-2.5-pro`** (returns HTTP 429 RESOURCE_EXHAUSTED immediately). `gemini-2.5-flash` works on the free tier. If the API key is upgraded to a paid plan, `_MODEL` in `src/reporter_gemini.py` can be switched back to `gemini-2.5-pro` for higher-quality reports.

**`src/reporter.py` (Claude/Anthropic) is kept as a reference/fallback** — switch `main.py`'s import back to `from src.reporter import CritiqueAgent, ReportingAgent` and ensure `ANTHROPIC_API_KEY` is set in `.env` to revert to Claude.

**Intermediate cache:** Every run saves `data/analysis_results.json`. Use `--skip-fetch` to regenerate reports from it without network calls.

---

## Key Design Decisions

- **Dual forecast always runs.** ARIMA and Prophet both execute; linear regression is only the emergency fallback. The `agreement` field ("converging" / "diverging" / "neutral") in `ForecastResult` is a confidence signal.
- **Benchmarks are context, not peers.** SPY/QQQ/DIA/IWM appear in the Market Context section of reports but never in the Investment Sentiment table. This is enforced in the reporter system prompt, not in code.
- **ETF vs stock routing** is handled in `FundamentalAnalyzer.analyze()` via the `is_benchmark` flag on `RawTickerData`. If you add new ETFs to a watchlist, mark them as benchmarks or they'll be analyzed as stocks.
- **Sector comparison** in `FundamentalAnalyzer._sector_averages()` averages P/E and P/B across same-sector peers in the current run. With small watchlists, this falls back to market defaults (P/E ≈ 22, P/B ≈ 4).

---

## Report Structure

**Main report** (`report_gemini.html`) — blue theme, 5 sections:
1. Executive Summary
2. Market Context (benchmark ETFs)
3. Stock-by-Stock Analysis (trend, forecast comparison, key risks)
4. Investment Sentiment table (Ticker | Sentiment | Rationale | Key Risk)
5. Methodology Notes

**Critique report** (`critique_gemini.html`) — red/amber theme, 7 sections:
1. Overconfidence Flags
2. Data Blind Spots
3. Model Limitations (ARIMA + Prophet)
4. Fundamental Analysis Gaps
5. Benchmark Context Limitations
6. Contradictions
7. Risk Score Table

---

## Feature Backlog (Priority Order)

These are the four data points the user identified as most impactful for pre-market decision-making — none are currently in the app:

### 1. IV Rank / Implied Volatility Context *(highest priority for directional options)*
- **Why:** Buying calls/puts when IV rank is high (>60) = expensive premium = bad risk/reward even if direction is right.
- **Implementation:** yfinance provides `impliedVolatility` on options chains via `ticker.options` and `ticker.option_chain()`. IV rank requires historical IV — could approximate with a rolling window of the ATM option's IV.
- **Where to add:** `src/fundamental.py` or a new `src/options.py`. Surface it prominently in the Stock-by-Stock section of the report.

### 2. Earnings Dates / Catalyst Calendar *(hard constraint for position sizing)*
- **Why:** Holding a short-swing directional option through earnings is a binary event. The app should flag if any ticker has earnings within the next 7 days.
- **Implementation:** `yfinance` provides `ticker.calendar` (returns a dict with `Earnings Date`). Already fetched in `fetcher.py` — just needs to be extracted and passed through.
- **Where to add:** `src/fundamental.py` — add `earnings_date: str | None` to `StockFundamentals`. Surface as a warning in the report: ⚠️ EARNINGS IN HOLD WINDOW.

### 3. Sector Rotation Signals
- **Why:** Knowing which sectors are gaining/losing relative to SPY helps confirm or contradict individual stock momentum.
- **Implementation:** Add sector ETFs (XLK, XLF, XLE, XLV, XLI, XLC, XLY, XLP, XLB, XLRE, XLU) to a "sector benchmark" group. Compute each sector ETF's 20-day return vs SPY. Surface a heatmap-style table in the Market Context section.
- **Where to add:** New section in `config.py` (`SECTOR_ETFS`), extend `src/fundamental.py` for sector ETF handling, and surface in `src/reporter.py` prompt.

### 4. Options Flow / Unusual Activity
- **Why:** Large block trades and unusual OI spikes can signal institutional positioning ahead of a move.
- **Caveat:** yfinance does not provide real-time options flow. This would require a paid data source (e.g., Unusual Whales API, Market Chameleon, or a broker API). Flag this as a future integration rather than a yfinance-based feature.
- **Interim approach:** yfinance does provide options chain data (OI, volume per strike). Could flag strikes where volume > OI as a weak "unusual activity" signal.

---

## Dependencies

Dependencies live in a dedicated conda environment, `finance_ai_agent` (Python 3.11), already created and installed.

```bash
# One-time setup (already done):
~/anaconda3/bin/conda create -n finance_ai_agent python=3.11 -y
~/anaconda3/envs/finance_ai_agent/bin/pip install -r requirements.txt
```

First install may take 3–5 minutes — Prophet pulls in `pystan`/`cmdstanpy` which compiles C++.

**Key packages:**
- `yfinance` — price history + fundamentals
- `ta` — technical indicators (pure Python)
- `statsmodels` — ARIMA
- `prophet` — Facebook Prophet forecasting
- `google-genai` — Gemini API (requires `GEMINI_API_KEY` in `.env`) — active provider
- `anthropic` — Claude API (requires `ANTHROPIC_API_KEY` in `.env`) — only needed if reverting to `src/reporter.py`
- `markdown` — Markdown → HTML conversion

---

## Environment

```bash
cp .env.example .env
# Add your key:
echo "GEMINI_API_KEY=..." >> .env
```

`load_dotenv()` is called in `main.py` before any imports that use the key.

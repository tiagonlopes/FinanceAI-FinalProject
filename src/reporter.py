import logging
import os
from datetime import datetime

import anthropic
import markdown as md_lib

from config import CLAUDE_MAX_TOKENS

_MODEL = "claude-opus-4-8"

logger = logging.getLogger("stock_analyzer.reporter")

_REPORT_SYSTEM = """You are a senior financial analyst producing a professional investment research report. Your analysis must:

- Cite specific numbers from the data provided (prices, P/E ratios, growth rates, etc.)
- Treat SPY, QQQ, DIA, and IWM strictly as market context benchmarks — do NOT include them in the Investment Sentiment table or stock valuation comparisons
- Explain ARIMA vs Prophet forecast agreement/disagreement and what it means for forecast confidence
- Maintain a balanced, evidence-based tone — neither excessively bullish nor bearish
- Keep the Executive Summary under 300 words
- For each non-benchmark stock, reference the provided rule-based "bullish probability" (5-day, technical-only) score, and state whether the other evidence (fundamentals, forecast agreement, momentum) corroborates or contradicts that lean
- Be concise throughout — prefer short bullet points over prose paragraphs

Produce a report with exactly these five sections (use H2 headers):
1. Executive Summary — market overview and top themes across all analyzed stocks
2. Market Context — what SPY, QQQ, DIA, and IWM signal about current market conditions
3. Stock-by-Stock Analysis — for each non-benchmark stock, use an H3 with the ticker symbol followed by AT MOST 5 bullet points total covering (only) the most decision-relevant points: trend/technical signal, 5-day forecast (ARIMA vs Prophet + bullish probability), valuation read (trailing P/E vs forward P/E vs PEG ratio — is the market pricing in earnings growth, and does the PEG suggest the multiple is justified by that growth?), and key risk. Do not write paragraphs — every point must be its own bullet. Cut anything that doesn't change the trade decision.
4. Investment Sentiment — markdown table: Ticker | Sentiment | Rationale | Key Risk (non-benchmark stocks only)
5. Methodology Notes — data sources, forecast models, caveats, and limitations (brief bullet list)

Use markdown formatting throughout."""

_CRITIQUE_SYSTEM = """You are a highly skeptical senior risk analyst whose job is to find flaws, blind spots, and overconfident conclusions in investment research reports. You are NOT trying to validate the report — you are trying to break it.

Your goal is to protect decision-makers from acting on incomplete or misleading analysis. Be specific: quote the report's claims and explain precisely why they are overconfident, unsupported, or missing context. Do not be diplomatic.

Be concise throughout — prefer short bullet points over prose paragraphs. Where a section discusses individual stocks, group findings under an H3 per ticker with AT MOST 5 bullet points total for that ticker — keep only the sharpest, most decision-relevant flaws and cut the rest.

Produce a critique with exactly these seven sections (use H2 headers):
1. Overconfidence Flags — claims stated with more certainty than the data supports, especially forecasts and sentiment ratings
2. Data Blind Spots — what the analysis cannot see: earnings surprises, macro events, geopolitical risk, management changes, regulatory risk, analyst downgrades, insider activity
3. Model Limitations — specific weaknesses of ARIMA and Prophet: non-stationarity, regime changes, fat tails, the fact that price history doesn't predict earnings catalysts. Also assess the rule-based "bullish probability" score (a weighted heuristic over RSI, MACD, SMA stack, Bollinger %B, ADX-based confidence damping, and forecast direction/agreement) — explain why it is not a statistically calibrated probability and what would be needed to validate it
4. Fundamental Analysis Gaps — missing metrics: free cash flow yield, insider ownership, short interest, institutional ownership changes, competitive moat, earnings quality
5. Benchmark Context Limitations — ways the ETF benchmarks may mislead: QQQ concentration, SPY sector weightings, what ETF performance cannot tell you about individual stock risk
6. Contradictions — technical vs fundamental signal conflicts the report glossed over or failed to reconcile, organized per-stock (H3 per ticker, max 5 bullets each)
7. Risk Score Table — markdown table: Ticker | Risk Level (Low / Medium / High) | Top 2 Risks (non-benchmark stocks only)

Be blunt, be specific, be adversarial."""


def _pct(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _cap(value) -> str:
    if value is None:
        return "N/A"
    try:
        b = float(value) / 1e9
        return f"${b:.1f}B"
    except (TypeError, ValueError):
        return "N/A"


def _f(value, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _build_data_section(analysis_json: dict) -> str:
    tickers = analysis_json.get("tickers", [])
    tech_map = {t["symbol"]: t for t in analysis_json.get("technical", [])}
    fund_map = {f["symbol"]: f for f in analysis_json.get("fundamental", [])}

    lines: list[str] = []

    lines += ["## TECHNICAL DATA", ""]
    for sym in tickers:
        tech = tech_map.get(sym, {})
        fund = fund_map.get(sym, {})
        is_bench = fund.get("is_benchmark", False)
        label = "  [BENCHMARK ETF]" if is_bench else ""

        lines.append(f"### {sym}{label}")
        lines.append(f"- Date: {tech.get('as_of_date', 'N/A')}")
        lines.append(f"- Price: ${_f(tech.get('current_price'))}")
        lines.append(f"- Trend: {tech.get('trend_direction', 'N/A')}")
        lines.append(f"- RSI(14): {_f(tech.get('rsi_14'))}")
        lines.append(
            f"- SMA20={_f(tech.get('sma_20'))}  SMA50={_f(tech.get('sma_50'))}  SMA200={_f(tech.get('sma_200'))}"
        )
        lines.append(
            f"- EMA20={_f(tech.get('ema_20'))}"
        )
        lines.append(
            f"- MACD line={_f(tech.get('macd_line'), 4)}  signal={_f(tech.get('macd_signal'), 4)}  hist={_f(tech.get('macd_histogram'), 4)}"
        )
        lines.append(
            f"- Bollinger upper={_f(tech.get('bb_upper'))}  mid={_f(tech.get('bb_middle'))}  lower={_f(tech.get('bb_lower'))}  %B={_f(tech.get('bb_pct'), 3)}"
        )
        lines.append(f"- ADX: {_f(tech.get('adx'))}")
        lines.append(
            f"- Support={_f(tech.get('support'))}  Resistance={_f(tech.get('resistance'))}"
        )
        lines.append(f"- Volume ratio vs 20d avg: {_f(tech.get('volume_ratio'))}")

        forecast = tech.get("forecast") or {}
        if forecast:
            lines.append(f"- Forecast primary model: {forecast.get('primary_model', 'N/A')}")
            lines.append(f"- Forecast agreement: {forecast.get('agreement', 'N/A')}")
            lines.append(f"- {forecast.get('comparison_narrative', '')}")

            arima = forecast.get("arima") or {}
            if arima.get("prices"):
                prices_str = ", ".join(
                    f"${p:.2f}" if p is not None else "N/A"
                    for p in arima["prices"]
                )
                lines.append(f"- ARIMA 5-day: [{prices_str}]")

            prophet = forecast.get("prophet") or {}
            if prophet.get("prices"):
                prices_str = ", ".join(
                    f"${p:.2f}" if p is not None else "N/A"
                    for p in prophet["prices"]
                )
                lines.append(f"- Prophet 5-day: [{prices_str}]")

            ensemble = forecast.get("ensemble_prices") or []
            if ensemble:
                prices_str = ", ".join(
                    f"${p:.2f}" if p is not None else "N/A"
                    for p in ensemble
                )
                lines.append(f"- Ensemble (mean) 5-day: [{prices_str}]")

            fallback = forecast.get("fallback") or {}
            if fallback.get("prices"):
                lines.append(f"- Fallback model: {fallback.get('model', 'N/A')}")

        lines.append(f"- Technical narrative: {tech.get('narrative', 'N/A')}")

        prob = tech.get("probability") or {}
        if prob:
            lines.append(
                f"- Bullish probability (5-day, rule-based technical score): "
                f"{_f(prob.get('bullish_probability'), 0)}% bullish / "
                f"{_f(prob.get('bearish_probability'), 0)}% bearish"
            )
            lines.append(f"- Probability summary: {prob.get('summary', 'N/A')}")

        lines.append("")

    lines += ["## FUNDAMENTAL DATA", ""]
    for sym in tickers:
        fund = fund_map.get(sym, {})
        is_bench = fund.get("is_benchmark", False)
        label = "  [BENCHMARK ETF]" if is_bench else ""

        lines.append(f"### {sym}{label}")

        if is_bench:
            lines.append(f"- Name: {fund.get('name', 'N/A')}")
            lines.append(f"- 6-month return: {_pct(fund.get('ytd_return'))}")
            lines.append(f"- 1-year return: {_pct(fund.get('one_year_return'))}")
            lines.append(f"- 3-year avg return: {_pct(fund.get('three_year_return'))}")
            lines.append(f"- Expense ratio: {_pct(fund.get('expense_ratio'))}")
            lines.append(f"- Dividend yield: {_pct(fund.get('dividend_yield'))}")
            total_b = fund.get("total_assets_billions")
            lines.append(f"- AUM: {f'${total_b:.1f}B' if total_b else 'N/A'}")
        else:
            lines.append(f"- Company: {fund.get('company_name', 'N/A')}")
            lines.append(f"- Sector: {fund.get('sector', 'N/A')} / {fund.get('industry', 'N/A')}")
            lines.append(f"- Market cap: {_cap(fund.get('market_cap'))}")
            lines.append(
                f"- P/E trailing: {_f(fund.get('pe_trailing'))}  "
                f"(sector avg: {_f(fund.get('sector_pe_avg'))}, vs sector: {fund.get('pe_vs_sector', 'N/A')})"
            )
            lines.append(
                f"- P/E forward: {_f(fund.get('pe_forward'))}  "
                f"({fund.get('pe_forward_vs_trailing', 'N/A')})"
            )
            lines.append(f"- PEG ratio (P/E to growth): {_f(fund.get('peg_ratio'))}")
            lines.append(
                f"- P/B: {_f(fund.get('pb'))}  P/S: {_f(fund.get('ps'))}  EV/EBITDA: {_f(fund.get('ev_ebitda'))}"
            )
            lines.append(
                f"- Revenue growth YoY: {_pct(fund.get('revenue_growth'))}"
            )
            lines.append(
                f"- Earnings growth YoY: {_pct(fund.get('earnings_growth'))}"
            )
            lines.append(f"- Profit margin: {_pct(fund.get('profit_margin'))}")
            lines.append(
                f"- Debt/Equity: {_f(fund.get('debt_equity'))}  Current ratio: {_f(fund.get('current_ratio'))}"
            )
            lines.append(f"- Dividend yield: {_pct(fund.get('dividend_yield'))}")
            lines.append(f"- Beta: {_f(fund.get('beta'))}")
            lines.append(
                f"- 52W high: {_f(fund.get('fifty_two_week_high'))}  52W low: {_f(fund.get('fifty_two_week_low'))}"
            )

        lines.append(f"- Fundamental narrative: {fund.get('narrative', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


def _wrap_html(body: str, title: str, theme: str = "blue") -> str:
    if theme == "blue":
        accent = "#1a4a7a"
        header_bg = "#1f6dad"
        border_color = "#2980b9"
        th_bg = "#1f6dad"
    else:
        accent = "#7b241c"
        header_bg = "#c0392b"
        border_color = "#e74c3c"
        th_bg = "#c0392b"

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    max-width: 1100px;
    margin: 0 auto;
    padding: 40px 24px;
    color: #2c3e50;
    line-height: 1.7;
    background: #f0f2f5;
  }}
  .report-header {{
    background: {header_bg};
    color: white;
    padding: 28px 32px;
    border-radius: 10px;
    margin-bottom: 36px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  }}
  .report-header h1 {{ margin: 0 0 8px 0; font-size: 1.8em; color: white; border: none; }}
  .report-header p {{ margin: 0; opacity: 0.85; font-size: 0.9em; }}
  .content {{ background: white; padding: 40px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  h1 {{ color: {accent}; border-bottom: 3px solid {border_color}; padding-bottom: 10px; font-size: 1.6em; }}
  h2 {{ color: {accent}; border-left: 5px solid {border_color}; padding-left: 14px; margin-top: 44px; font-size: 1.3em; }}
  h3 {{ color: {accent}; margin-top: 28px; font-size: 1.1em; }}
  table {{ width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 0.95em; }}
  th {{ background: {th_bg}; color: white; padding: 12px 14px; text-align: left; font-weight: 600; }}
  td {{ padding: 10px 14px; border-bottom: 1px solid #e8ecf0; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #f8f9fa; }}
  code {{ background: #eef2f7; padding: 2px 6px; border-radius: 4px; font-size: 0.88em; font-family: 'SF Mono', 'Fira Code', monospace; }}
  pre {{ background: #2c3e50; color: #ecf0f1; padding: 18px; border-radius: 8px; overflow-x: auto; }}
  pre code {{ background: none; color: inherit; }}
  blockquote {{ border-left: 4px solid {border_color}; margin: 0; padding: 10px 18px; background: #f4f8fc; color: #5d6d7e; }}
  ul, ol {{ padding-left: 24px; }}
  li {{ margin-bottom: 4px; }}
  strong {{ color: #1a2a3a; }}
  .footer {{ color: #95a5a6; font-size: 0.82em; margin-top: 44px; padding-top: 20px; border-top: 1px solid #e8ecf0; text-align: center; }}
  hr {{ border: none; border-top: 1px solid #e8ecf0; margin: 32px 0; }}
  .chart-container {{ margin: 16px 0 28px 0; border: 1px solid #e8ecf0; border-radius: 8px; padding: 8px; }}
  .chart-gauge {{ max-width: 420px; }}
  .chart-row {{ display: flex; flex-wrap: wrap; gap: 16px; }}
  .chart-row .chart-container {{ flex: 1 1 0; min-width: 320px; margin: 16px 0 28px 0; }}
  .prob-summary {{ color: #5d6d7e; margin-top: -16px; margin-bottom: 28px; }}
  h4 {{ color: {accent}; margin-top: 32px; margin-bottom: 4px; font-size: 1.0em; }}
</style>
</head>
<body>
<div class="report-header">
  <h1>{title}</h1>
  <p>Generated: {generated}</p>
</div>
<div class="content">
{body}
</div>
<p class="footer">Stock Analyzer AI — Data sourced from Yahoo Finance. For informational purposes only.</p>
</body>
</html>"""


class ReportingAgent:
    def __init__(self):
        self._client = anthropic.Anthropic()

    def generate(self, analysis_json: dict) -> str:
        tickers = analysis_json.get("tickers", [])
        fund_map = {f["symbol"]: f for f in analysis_json.get("fundamental", [])}
        stocks = [s for s in tickers if not fund_map.get(s, {}).get("is_benchmark", False)]
        benchmarks = [s for s in tickers if fund_map.get(s, {}).get("is_benchmark", False)]

        data_section = _build_data_section(analysis_json)

        user_prompt = "\n".join([
            "Analyze the following market data and produce a professional investment research report.",
            "",
            f"**Non-benchmark stocks:** {', '.join(stocks)}",
            f"**Market benchmarks (context only, exclude from Investment Sentiment):** {', '.join(benchmarks)}",
            "",
            "---",
            "",
            data_section,
            "",
            "---",
            "",
            "Now write the full 5-section report as specified.",
        ])

        collected: list[str] = []
        print("\n" + "=" * 60)
        print("GENERATING MAIN REPORT  (streaming)")
        print("=" * 60 + "\n")

        with self._client.messages.stream(
            model=_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=_REPORT_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                collected.append(text)

        print("\n")
        logger.info("Main report generation complete.")
        return "".join(collected)

    def save(self, report_md: str, output_dir: str) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)

        md_path = os.path.join(output_dir, "report.md")
        html_path = os.path.join(output_dir, "report.html")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report_md)

        html_body = md_lib.markdown(
            report_md,
            extensions=["tables", "fenced_code"],
        )
        html_full = _wrap_html(html_body, title="Stock Analysis Report", theme="blue")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_full)

        logger.info(f"Report saved → {md_path}  {html_path}")
        return {"md": md_path, "html": html_path}


class CritiqueAgent:
    def __init__(self):
        self._client = anthropic.Anthropic()

    def generate(self, report_md: str, analysis_json: dict) -> str:
        tickers = analysis_json.get("tickers", [])
        fund_map = {f["symbol"]: f for f in analysis_json.get("fundamental", [])}
        tech_map = {t["symbol"]: t for t in analysis_json.get("technical", [])}
        stocks = [s for s in tickers if not fund_map.get(s, {}).get("is_benchmark", False)]

        meta_lines: list[str] = []
        for sym in stocks:
            tech = tech_map.get(sym, {})
            fund = fund_map.get(sym, {})
            forecast = tech.get("forecast") or {}
            agreement = forecast.get("agreement", "N/A")
            prob = tech.get("probability") or {}
            meta_lines.append(
                f"- **{sym}**: trend={tech.get('trend_direction','?')}  "
                f"RSI={_f(tech.get('rsi_14'))}  "
                f"forecast_agreement={agreement}  "
                f"bullish_probability={_f(prob.get('bullish_probability'), 0)}%  "
                f"pe_vs_sector={fund.get('pe_vs_sector','?')}  "
                f"pe_forward={_f(fund.get('pe_forward'))}  "
                f"peg_ratio={_f(fund.get('peg_ratio'))}  "
                f"revenue_growth={_pct(fund.get('revenue_growth'))}  "
                f"profit_margin={_pct(fund.get('profit_margin'))}"
            )

        user_prompt = "\n".join([
            "You have been given an investment research report to critique adversarially.",
            "Find every flaw, blind spot, and overconfident claim.",
            "",
            f"**Stocks covered (non-benchmark):** {', '.join(stocks)}",
            "",
            "---",
            "## THE REPORT TO CRITIQUE",
            "",
            report_md,
            "",
            "---",
            "## KEY RAW DATA POINTS (use to identify misrepresentations or omissions)",
            "",
        ] + meta_lines + [
            "",
            "---",
            "",
            "Now produce your 7-section adversarial critique.",
        ])

        collected: list[str] = []
        print("\n" + "=" * 60)
        print("GENERATING CRITIQUE REPORT  (streaming)")
        print("=" * 60 + "\n")

        with self._client.messages.stream(
            model=_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=_CRITIQUE_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                collected.append(text)

        print("\n")
        logger.info("Critique generation complete.")
        return "".join(collected)

    def save(self, critique_md: str, output_dir: str) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)

        md_path = os.path.join(output_dir, "critique.md")
        html_path = os.path.join(output_dir, "critique.html")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(critique_md)

        html_body = md_lib.markdown(
            critique_md,
            extensions=["tables", "fenced_code"],
        )
        html_full = _wrap_html(
            html_body, title="Stock Analysis — Critique Report", theme="red"
        )

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_full)

        logger.info(f"Critique saved → {md_path}  {html_path}")
        return {"md": md_path, "html": html_path}

import logging
import os
from datetime import datetime

import markdown as md_lib
from google import genai
from google.genai import types

from src.reporter import (
    _CRITIQUE_SYSTEM,
    _REPORT_SYSTEM,
    _build_data_section,
    _f,
    _pct,
    _wrap_html,
)

_MODEL = "gemini-2.5-flash"
_MAX_OUTPUT_TOKENS = 8192

logger = logging.getLogger("stock_analyzer.reporter_gemini")


class ReportingAgent:
    def __init__(self):
        self._client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

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
        print("GENERATING MAIN REPORT (Gemini, streaming)")
        print("=" * 60 + "\n")

        stream = self._client.models.generate_content_stream(
            model=_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=_REPORT_SYSTEM,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            ),
        )
        for chunk in stream:
            if chunk.text:
                print(chunk.text, end="", flush=True)
                collected.append(chunk.text)

        print("\n")
        logger.info("Main report generation complete.")
        return "".join(collected)

    def save(self, report_md: str, output_dir: str, analysis_json: dict | None = None) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        md_path = os.path.join(output_dir, f"report_gemini_{timestamp}.md")
        html_path = os.path.join(output_dir, f"report_gemini_{timestamp}.html")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report_md)

        html_body = md_lib.markdown(
            report_md,
            extensions=["tables", "fenced_code"],
        )

        if analysis_json is not None:
            from src.charts import build_charts_section
            html_body += "\n" + build_charts_section(analysis_json)

        html_full = _wrap_html(html_body, title="Stock Analysis Report (Gemini)", theme="blue")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_full)

        logger.info(f"Report saved → {md_path}  {html_path}")
        return {"md": md_path, "html": html_path}


class CritiqueAgent:
    def __init__(self):
        self._client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

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
        print("GENERATING CRITIQUE REPORT (Gemini, streaming)")
        print("=" * 60 + "\n")

        stream = self._client.models.generate_content_stream(
            model=_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=_CRITIQUE_SYSTEM,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            ),
        )
        for chunk in stream:
            if chunk.text:
                print(chunk.text, end="", flush=True)
                collected.append(chunk.text)

        print("\n")
        logger.info("Critique generation complete.")
        return "".join(collected)

    def save(self, critique_md: str, output_dir: str) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        md_path = os.path.join(output_dir, f"critique_gemini_{timestamp}.md")
        html_path = os.path.join(output_dir, f"critique_gemini_{timestamp}.html")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(critique_md)

        html_body = md_lib.markdown(
            critique_md,
            extensions=["tables", "fenced_code"],
        )
        html_full = _wrap_html(
            html_body, title="Stock Analysis — Critique Report (Gemini)", theme="red"
        )

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_full)

        logger.info(f"Critique saved → {md_path}  {html_path}")
        return {"md": md_path, "html": html_path}

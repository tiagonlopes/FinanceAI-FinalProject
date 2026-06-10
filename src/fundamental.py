import logging
from dataclasses import dataclass

import pandas as pd

from config import MARKET_DEFAULT_PB, MARKET_DEFAULT_PE, SECTOR_COMPARISON_THRESHOLD
from src.utils import safe_float

logger = logging.getLogger("stock_analyzer.fundamental")

_INFO_KEYS = {
    "company_name": "longName",
    "sector": "sector",
    "industry": "industry",
    "market_cap": "marketCap",
    "pe_trailing": "trailingPE",
    "pe_forward": "forwardPE",
    "peg_ratio": "trailingPegRatio",
    "pb": "priceToBook",
    "ps": "priceToSalesTrailing12Months",
    "ev_ebitda": "enterpriseToEbitda",
    "revenue_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth",
    "profit_margin": "profitMargins",
    "debt_equity": "debtToEquity",
    "current_ratio": "currentRatio",
    "dividend_yield": "dividendYield",
    "beta": "beta",
    "fifty_two_week_high": "fiftyTwoWeekHigh",
    "fifty_two_week_low": "fiftyTwoWeekLow",
}

_BENCHMARK_KEYS = {
    "name": "longName",
    "expense_ratio": "annualReportExpenseRatio",
    "dividend_yield": "dividendYield",
    "ytd_return": "ytdReturn",
    "one_year_return": "oneYearReturn",
    "three_year_return": "threeYearAverageReturn",
    "nav": "navPrice",
    "total_assets": "totalAssets",
}


@dataclass
class StockFundamentals:
    symbol: str
    company_name: str | None
    sector: str | None
    industry: str | None
    market_cap: float | None
    pe_trailing: float | None
    pe_forward: float | None
    peg_ratio: float | None
    pe_forward_vs_trailing: str | None
    pb: float | None
    ps: float | None
    ev_ebitda: float | None
    revenue_growth: float | None
    earnings_growth: float | None
    profit_margin: float | None
    debt_equity: float | None
    current_ratio: float | None
    dividend_yield: float | None
    beta: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    sector_pe_avg: float | None
    sector_pb_avg: float | None
    pe_vs_sector: str | None
    is_benchmark: bool = False
    narrative: str = ""


@dataclass
class BenchmarkFundamentals:
    symbol: str
    name: str | None
    expense_ratio: float | None
    dividend_yield: float | None
    ytd_return: float | None
    one_year_return: float | None
    three_year_return: float | None
    nav: float | None
    total_assets_billions: float | None
    is_benchmark: bool = True
    narrative: str = ""


class FundamentalAnalyzer:
    def analyze(self, symbol: str, raw, all_raw: list) -> StockFundamentals | BenchmarkFundamentals:
        if raw["is_benchmark"]:
            return self._extract_benchmark(symbol, raw["info"], raw["price_history"])
        return self._extract_stock(symbol, raw["info"], raw["price_history"], all_raw)

    def _extract_benchmark(
        self, symbol: str, info: dict, history: pd.DataFrame
    ) -> BenchmarkFundamentals:
        def g(key):
            return safe_float(info.get(_BENCHMARK_KEYS.get(key, key)))

        total_assets = g("total_assets")
        total_assets_b = safe_float(total_assets / 1e9) if total_assets else None

        # Compute YTD and 1-year return from price history if not in .info
        ytd_return = g("ytd_return")
        one_year_return = g("one_year_return")
        if history is not None and not history.empty:
            close = history["Close"]
            if ytd_return is None and len(close) >= 2:
                ytd_return = safe_float((close.iloc[-1] - close.iloc[0]) / close.iloc[0])
            if one_year_return is None and len(close) >= 2:
                one_year_return = ytd_return  # approximate with available window

        result = BenchmarkFundamentals(
            symbol=symbol,
            name=info.get(_BENCHMARK_KEYS["name"]),
            expense_ratio=g("expense_ratio"),
            dividend_yield=g("dividend_yield"),
            ytd_return=ytd_return,
            one_year_return=one_year_return,
            three_year_return=g("three_year_return"),
            nav=g("nav"),
            total_assets_billions=total_assets_b,
        )
        result.narrative = self._benchmark_narrative(result)
        return result

    def _extract_stock(
        self, symbol: str, info: dict, history: pd.DataFrame, all_raw: list
    ) -> StockFundamentals:
        def g(key):
            yf_key = _INFO_KEYS.get(key, key)
            return safe_float(info.get(yf_key)) if key not in ("company_name", "sector", "industry") \
                else info.get(yf_key)

        sector = info.get(_INFO_KEYS["sector"])
        sector_pe, sector_pb = self._sector_averages(sector, symbol, all_raw)

        pe_trailing = safe_float(info.get(_INFO_KEYS["pe_trailing"]))
        pe_forward = safe_float(info.get(_INFO_KEYS["pe_forward"]))
        pe_vs_sector = self._vs_sector(pe_trailing, sector_pe)

        earnings_growth = safe_float(info.get(_INFO_KEYS["earnings_growth"]))
        peg_ratio = safe_float(info.get(_INFO_KEYS["peg_ratio"]))
        if peg_ratio is None and pe_trailing is not None and earnings_growth and earnings_growth > 0:
            peg_ratio = pe_trailing / (earnings_growth * 100)

        pe_forward_vs_trailing = self._pe_forward_vs_trailing(pe_trailing, pe_forward)

        result = StockFundamentals(
            symbol=symbol,
            company_name=info.get(_INFO_KEYS["company_name"]),
            sector=sector,
            industry=info.get(_INFO_KEYS["industry"]),
            market_cap=safe_float(info.get(_INFO_KEYS["market_cap"])),
            pe_trailing=pe_trailing,
            pe_forward=pe_forward,
            peg_ratio=peg_ratio,
            pe_forward_vs_trailing=pe_forward_vs_trailing,
            pb=safe_float(info.get(_INFO_KEYS["pb"])),
            ps=safe_float(info.get(_INFO_KEYS["ps"])),
            ev_ebitda=safe_float(info.get(_INFO_KEYS["ev_ebitda"])),
            revenue_growth=safe_float(info.get(_INFO_KEYS["revenue_growth"])),
            earnings_growth=earnings_growth,
            profit_margin=safe_float(info.get(_INFO_KEYS["profit_margin"])),
            debt_equity=safe_float(info.get(_INFO_KEYS["debt_equity"])),
            current_ratio=safe_float(info.get(_INFO_KEYS["current_ratio"])),
            dividend_yield=safe_float(info.get(_INFO_KEYS["dividend_yield"])),
            beta=safe_float(info.get(_INFO_KEYS["beta"])),
            fifty_two_week_high=safe_float(info.get(_INFO_KEYS["fifty_two_week_high"])),
            fifty_two_week_low=safe_float(info.get(_INFO_KEYS["fifty_two_week_low"])),
            sector_pe_avg=sector_pe,
            sector_pb_avg=sector_pb,
            pe_vs_sector=pe_vs_sector,
        )
        result.narrative = self._stock_narrative(result)
        return result

    def _sector_averages(
        self, sector: str | None, exclude_symbol: str, all_raw: list
    ) -> tuple[float | None, float | None]:
        if sector is None:
            return MARKET_DEFAULT_PE, MARKET_DEFAULT_PB

        peers_pe = []
        peers_pb = []
        for raw in all_raw:
            if raw["is_benchmark"] or raw["symbol"] == exclude_symbol:
                continue
            peer_info = raw.get("info", {})
            if peer_info.get("sector") == sector:
                pe = safe_float(peer_info.get("trailingPE"))
                pb = safe_float(peer_info.get("priceToBook"))
                if pe is not None:
                    peers_pe.append(pe)
                if pb is not None:
                    peers_pb.append(pb)

        sector_pe = (sum(peers_pe) / len(peers_pe)) if peers_pe else MARKET_DEFAULT_PE
        sector_pb = (sum(peers_pb) / len(peers_pb)) if peers_pb else MARKET_DEFAULT_PB
        return sector_pe, sector_pb

    def _pe_forward_vs_trailing(self, pe_trailing: float | None, pe_forward: float | None) -> str | None:
        if pe_trailing is None or pe_forward is None or pe_trailing == 0:
            return None
        delta = (pe_forward - pe_trailing) / pe_trailing
        if delta < -0.05:
            return "earnings expected to grow (forward P/E below trailing)"
        if delta > 0.05:
            return "earnings expected to decline (forward P/E above trailing)"
        return "earnings expected roughly flat (forward P/E close to trailing)"

    def _vs_sector(self, value: float | None, sector_avg: float | None) -> str | None:
        if value is None or sector_avg is None or sector_avg == 0:
            return None
        ratio = (value - sector_avg) / sector_avg
        if ratio > SECTOR_COMPARISON_THRESHOLD:
            return "premium"
        if ratio < -SECTOR_COMPARISON_THRESHOLD:
            return "discount"
        return "in-line"

    def _stock_narrative(self, r: StockFundamentals) -> str:
        parts = []

        if r.pe_trailing is not None:
            label = f"P/E of {r.pe_trailing:.1f}"
            if r.pe_vs_sector:
                label += f" ({r.pe_vs_sector} vs sector avg {r.sector_pe_avg:.1f})"
            if r.pe_forward is not None:
                label += f", forward P/E {r.pe_forward:.1f}"
            if r.pe_forward_vs_trailing:
                label += f" ({r.pe_forward_vs_trailing})"
            parts.append(label)

        if r.peg_ratio is not None:
            peg_label = "attractive" if r.peg_ratio < 1 else "reasonable" if r.peg_ratio < 2 else "expensive"
            parts.append(f"PEG ratio {r.peg_ratio:.2f} ({peg_label} relative to earnings growth)")

        if r.profit_margin is not None:
            parts.append(f"profit margin {r.profit_margin * 100:.1f}%")

        if r.revenue_growth is not None:
            parts.append(f"revenue growth {r.revenue_growth * 100:.1f}% YoY")

        if r.debt_equity is not None:
            de_label = "low" if r.debt_equity < 0.5 else "moderate" if r.debt_equity < 1.5 else "high"
            parts.append(f"{de_label} debt/equity ({r.debt_equity:.2f})")

        name = r.company_name or r.symbol
        if parts:
            return f"{name} shows {', '.join(parts)}."
        return f"Fundamental data limited for {name}."

    def _benchmark_narrative(self, r: BenchmarkFundamentals) -> str:
        name = r.name or r.symbol
        parts = []
        if r.ytd_return is not None:
            parts.append(f"6-month return {r.ytd_return * 100:.1f}%")
        if r.expense_ratio is not None:
            parts.append(f"expense ratio {r.expense_ratio * 100:.2f}%")
        if r.dividend_yield is not None:
            parts.append(f"yield {r.dividend_yield * 100:.2f}%")
        if parts:
            return f"{name}: {', '.join(parts)}."
        return f"{name}: benchmark ETF."

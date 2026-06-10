import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from config import BENCHMARK_TICKERS, MAX_CONCURRENT_FETCHES
from src.fetcher import MarketDataFetcher, RawTickerData
from src.fundamental import BenchmarkFundamentals, FundamentalAnalyzer, StockFundamentals
from src.technical import TechnicalAnalyzer, TechnicalResult

logger = logging.getLogger("stock_analyzer.runner")


@dataclass
class AnalysisResults:
    tickers: list[str]
    technical: list[TechnicalResult]
    fundamental: list[StockFundamentals | BenchmarkFundamentals]


async def run_analysis(user_tickers: list[str]) -> AnalysisResults:
    all_tickers = _merge_tickers(user_tickers)
    logger.info(f"Running analysis for: {all_tickers}")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
    fetcher = MarketDataFetcher(semaphore)

    logger.info("Phase 1: fetching market data...")
    raw_data: list[RawTickerData] = await asyncio.gather(
        *[fetcher.fetch_ticker(sym) for sym in all_tickers],
        return_exceptions=False,
    )

    logger.info("Phase 2: running technical and fundamental analysis...")
    tech_analyzer = TechnicalAnalyzer()
    fund_analyzer = FundamentalAnalyzer()

    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=min(len(raw_data) * 2, 16))

    async def analyze_ticker(raw: RawTickerData):
        sym = raw["symbol"]
        tech_future = loop.run_in_executor(
            executor, tech_analyzer.analyze, sym, raw["price_history"]
        )
        fund_future = loop.run_in_executor(
            executor, fund_analyzer.analyze, sym, raw, raw_data
        )
        tech_result, fund_result = await asyncio.gather(tech_future, fund_future)
        logger.info(f"Completed analysis for {sym}")
        return tech_result, fund_result

    pairs = await asyncio.gather(*[analyze_ticker(raw) for raw in raw_data])
    executor.shutdown(wait=False)

    technical = [p[0] for p in pairs]
    fundamental = [p[1] for p in pairs]

    return AnalysisResults(tickers=all_tickers, technical=technical, fundamental=fundamental)


def _merge_tickers(user_tickers: list[str]) -> list[str]:
    seen = set()
    result = []
    for t in user_tickers + BENCHMARK_TICKERS:
        t = t.upper()
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result

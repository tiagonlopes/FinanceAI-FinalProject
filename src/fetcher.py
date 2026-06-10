import asyncio
import logging
import time
from typing import TypedDict

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from config import BENCHMARK_TICKERS, FETCH_DELAY_SECONDS

logger = logging.getLogger("stock_analyzer.fetcher")


class RawTickerData(TypedDict):
    symbol: str
    is_benchmark: bool
    price_history: pd.DataFrame
    info: dict
    financials: pd.DataFrame
    balance_sheet: pd.DataFrame
    cashflow: pd.DataFrame


class MarketDataFetcher:
    def __init__(self, semaphore: asyncio.Semaphore):
        self._semaphore = semaphore

    async def fetch_ticker(self, symbol: str) -> RawTickerData:
        async with self._semaphore:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._fetch_sync, symbol)
            await asyncio.sleep(FETCH_DELAY_SECONDS)
            return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _fetch_sync(self, symbol: str) -> RawTickerData:
        logger.debug(f"Fetching {symbol}")
        ticker = yf.Ticker(symbol)

        history = ticker.history(period="6mo", auto_adjust=True)
        if history.empty:
            raise ValueError(f"No price history for {symbol}")

        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            logger.warning(f"{symbol}: could not fetch .info")

        financials = pd.DataFrame()
        balance_sheet = pd.DataFrame()
        cashflow = pd.DataFrame()

        if symbol not in BENCHMARK_TICKERS:
            try:
                financials = ticker.financials
            except Exception:
                pass
            try:
                balance_sheet = ticker.balance_sheet
            except Exception:
                pass
            try:
                cashflow = ticker.cashflow
            except Exception:
                pass

        logger.info(f"Fetched {symbol}: {len(history)} days of price data")
        return RawTickerData(
            symbol=symbol,
            is_benchmark=symbol in BENCHMARK_TICKERS,
            price_history=history,
            info=info,
            financials=financials,
            balance_sheet=balance_sheet,
            cashflow=cashflow,
        )

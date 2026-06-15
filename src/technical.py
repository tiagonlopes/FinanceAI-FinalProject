import logging
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import ta
from sklearn.linear_model import LinearRegression
from statsmodels.tsa.arima.model import ARIMA

from config import (
    ARIMA_LOOKBACK_DAYS,
    ARIMA_ORDER,
    CHART_HISTORY_DAYS,
    FORECAST_CI_ALPHA,
    FORECAST_DAYS,
    PROBABILITY_WEIGHTS,
    PROPHET_SEASONALITY_MODE,
)
from src.utils import safe_float

logger = logging.getLogger("stock_analyzer.technical")
warnings.filterwarnings("ignore", category=UserWarning)


@dataclass
class ModelForecast:
    model: str
    prices: list[float | None]
    lower: list[float | None]
    upper: list[float | None]


@dataclass
class ForecastResult:
    arima: ModelForecast | None
    prophet: ModelForecast | None
    fallback: ModelForecast | None
    ensemble_prices: list[float | None]
    agreement: str  # "converging" | "diverging" | "neutral" | "single_model"
    comparison_narrative: str
    primary_model: str


@dataclass
class ProbabilityResult:
    bullish_probability: float  # 0-100, probability price is higher in 5 days
    bearish_probability: float  # 0-100, = 100 - bullish_probability
    components: dict[str, float]  # component name -> weighted contribution in [-1, 1]
    summary: str


@dataclass
class TechnicalResult:
    symbol: str
    as_of_date: str
    current_price: float | None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ema_20: float | None = None
    rsi_14: float | None = None
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_pct: float | None = None
    volume_ratio: float | None = None
    support: float | None = None
    resistance: float | None = None
    adx: float | None = None
    trend_direction: str = "sideways"
    forecast: ForecastResult | None = None
    narrative: str = ""
    price_history: list[dict] = field(default_factory=list)
    probability: ProbabilityResult | None = None


class TechnicalAnalyzer:
    def analyze(self, symbol: str, df: pd.DataFrame) -> TechnicalResult:
        if df.empty:
            return TechnicalResult(symbol=symbol, as_of_date="", current_price=None)

        df = df.copy()
        df.index = pd.to_datetime(df.index)
        close = df["Close"]
        volume = df["Volume"] if "Volume" in df.columns else None

        result = TechnicalResult(
            symbol=symbol,
            as_of_date=df.index[-1].strftime("%Y-%m-%d"),
            current_price=safe_float(close.iloc[-1]),
        )

        result.sma_20 = self._sma(close, 20)
        result.sma_50 = self._sma(close, 50)
        result.sma_200 = self._sma(close, 200) if len(close) >= 200 else None
        result.ema_20 = self._ema(close, 20)
        result.rsi_14 = self._rsi(close)
        result.macd_line, result.macd_signal, result.macd_histogram = self._macd(close)
        result.bb_upper, result.bb_middle, result.bb_lower, result.bb_pct = self._bollinger(close)
        result.volume_ratio = self._volume_ratio(volume) if volume is not None else None
        result.support = safe_float(close.rolling(20).min().iloc[-1])
        result.resistance = safe_float(close.rolling(20).max().iloc[-1])
        result.adx = self._adx(df)
        result.trend_direction = self._trend_direction(close, result.sma_20, result.sma_50, result.adx)
        result.forecast = self._run_forecasts(close)
        result.narrative = self._narrative(result)
        result.price_history = self._price_history(close)
        result.probability = self._compute_probability(result)

        return result

    def _sma(self, close: pd.Series, window: int) -> float | None:
        if len(close) < window:
            return None
        ind = ta.trend.SMAIndicator(close, window=window)
        return safe_float(ind.sma_indicator().iloc[-1])

    def _ema(self, close: pd.Series, window: int) -> float | None:
        if len(close) < window:
            return None
        ind = ta.trend.EMAIndicator(close, window=window)
        return safe_float(ind.ema_indicator().iloc[-1])

    def _rsi(self, close: pd.Series) -> float | None:
        if len(close) < 14:
            return None
        ind = ta.momentum.RSIIndicator(close, window=14)
        return safe_float(ind.rsi().iloc[-1])

    def _macd(self, close: pd.Series) -> tuple[float | None, float | None, float | None]:
        if len(close) < 26:
            return None, None, None
        ind = ta.trend.MACD(close)
        return (
            safe_float(ind.macd().iloc[-1]),
            safe_float(ind.macd_signal().iloc[-1]),
            safe_float(ind.macd_diff().iloc[-1]),
        )

    def _bollinger(self, close: pd.Series) -> tuple[float | None, float | None, float | None, float | None]:
        if len(close) < 20:
            return None, None, None, None
        ind = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        upper = safe_float(ind.bollinger_hband().iloc[-1])
        middle = safe_float(ind.bollinger_mavg().iloc[-1])
        lower = safe_float(ind.bollinger_lband().iloc[-1])
        pct = safe_float(ind.bollinger_pband().iloc[-1])
        return upper, middle, lower, pct

    def _volume_ratio(self, volume: pd.Series) -> float | None:
        if len(volume) < 20:
            return None
        avg = volume.rolling(20).mean().iloc[-1]
        if avg == 0:
            return None
        return safe_float(volume.iloc[-1] / avg)

    def _adx(self, df: pd.DataFrame) -> float | None:
        if len(df) < 14 or not all(c in df.columns for c in ["High", "Low", "Close"]):
            return None
        ind = ta.trend.ADXIndicator(df["High"], df["Low"], df["Close"], window=14)
        return safe_float(ind.adx().iloc[-1])

    def _trend_direction(self, close, sma_20, sma_50, adx) -> str:
        if sma_20 is None or sma_50 is None:
            return "sideways"
        current = safe_float(close.iloc[-1])
        if current is None:
            return "sideways"
        if adx is not None and adx < 20:
            return "sideways"
        if current > sma_20 and sma_20 > sma_50:
            return "uptrend"
        if current < sma_20 and sma_20 < sma_50:
            return "downtrend"
        return "sideways"

    def _run_forecasts(self, close: pd.Series) -> ForecastResult:
        arima_result = self._forecast_arima(close)
        prophet_result = self._forecast_prophet(close)

        active = [m for m in [arima_result, prophet_result] if m is not None]

        if not active:
            fallback = self._forecast_linear(close)
            ensemble = fallback.prices if fallback else [None] * FORECAST_DAYS
            return ForecastResult(
                arima=None, prophet=None, fallback=fallback,
                ensemble_prices=ensemble, agreement="single_model",
                comparison_narrative="Both ARIMA and Prophet failed; using linear regression fallback.",
                primary_model="LinearRegression",
            )

        if len(active) == 1:
            ensemble = active[0].prices
            return ForecastResult(
                arima=arima_result, prophet=prophet_result, fallback=None,
                ensemble_prices=ensemble, agreement="single_model",
                comparison_narrative=f"Only {active[0].model} converged.",
                primary_model=active[0].model,
            )

        ensemble = self._ensemble(arima_result.prices, prophet_result.prices)
        agreement, narrative = self._compare_forecasts(
            close.iloc[-1], arima_result.prices, prophet_result.prices
        )
        return ForecastResult(
            arima=arima_result, prophet=prophet_result, fallback=None,
            ensemble_prices=ensemble, agreement=agreement,
            comparison_narrative=narrative,
            primary_model="Ensemble",
        )

    def _forecast_arima(self, close: pd.Series) -> ModelForecast | None:
        series = close.tail(ARIMA_LOOKBACK_DAYS).dropna()
        if len(series) < 20:
            return None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = ARIMA(series, order=ARIMA_ORDER)
                fit = model.fit()
                forecast = fit.get_forecast(steps=FORECAST_DAYS)
                pred = forecast.predicted_mean.tolist()
                ci = forecast.conf_int(alpha=FORECAST_CI_ALPHA)
                return ModelForecast(
                    model="ARIMA",
                    prices=[safe_float(p) for p in pred],
                    lower=[safe_float(v) for v in ci.iloc[:, 0].tolist()],
                    upper=[safe_float(v) for v in ci.iloc[:, 1].tolist()],
                )
        except Exception as e:
            logger.warning(f"ARIMA failed: {e}")
            return None

    def _forecast_prophet(self, close: pd.Series) -> ModelForecast | None:
        if len(close) < 30:
            return None
        try:
            from prophet import Prophet

            prophet_df = pd.DataFrame({
                "ds": close.index.tz_localize(None) if close.index.tz else close.index,
                "y": close.values,
            })
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                m = Prophet(
                    seasonality_mode=PROPHET_SEASONALITY_MODE,
                    daily_seasonality=False,
                    weekly_seasonality=True,
                    yearly_seasonality=True,
                    interval_width=1 - FORECAST_CI_ALPHA,
                )
                m.fit(prophet_df)
                future = m.make_future_dataframe(periods=FORECAST_DAYS, freq="B")
                forecast_df = m.predict(future)
                tail = forecast_df.tail(FORECAST_DAYS)
                return ModelForecast(
                    model="Prophet",
                    prices=[safe_float(v) for v in tail["yhat"].tolist()],
                    lower=[safe_float(v) for v in tail["yhat_lower"].tolist()],
                    upper=[safe_float(v) for v in tail["yhat_upper"].tolist()],
                )
        except Exception as e:
            logger.warning(f"Prophet failed: {e}")
            return None

    def _forecast_linear(self, close: pd.Series) -> ModelForecast | None:
        series = close.tail(30).dropna()
        if len(series) < 5:
            return None
        try:
            X = np.arange(len(series)).reshape(-1, 1)
            y = series.values
            reg = LinearRegression().fit(X, y)
            future_X = np.arange(len(series), len(series) + FORECAST_DAYS).reshape(-1, 1)
            pred = reg.predict(future_X).tolist()
            residuals = y - reg.predict(X)
            std = float(np.std(residuals))
            return ModelForecast(
                model="LinearRegression",
                prices=[safe_float(p) for p in pred],
                lower=[safe_float(p - 1.28 * std) for p in pred],
                upper=[safe_float(p + 1.28 * std) for p in pred],
            )
        except Exception as e:
            logger.warning(f"Linear regression failed: {e}")
            return None

    def _ensemble(self, a: list, b: list) -> list[float | None]:
        result = []
        for va, vb in zip(a, b):
            if va is not None and vb is not None:
                result.append((va + vb) / 2)
            else:
                result.append(va or vb)
        return result

    def _compare_forecasts(
        self, current_price, arima_prices: list, prophet_prices: list
    ) -> tuple[str, str]:
        current = float(current_price)
        a_end = arima_prices[-1]
        p_end = prophet_prices[-1]

        if a_end is None or p_end is None:
            return "neutral", "Could not compare forecasts due to missing values."

        a_pct = (a_end - current) / current * 100
        p_pct = (p_end - current) / current * 100
        spread = abs(a_pct - p_pct)

        same_direction = (a_pct >= 0) == (p_pct >= 0)

        if same_direction and spread < 5:
            agreement = "converging"
        elif not same_direction or spread > 10:
            agreement = "diverging"
        else:
            agreement = "neutral"

        direction_a = "recovery" if a_pct > 0 else "decline"
        direction_p = "recovery" if p_pct > 0 else "decline"

        if agreement == "converging":
            narrative = (
                f"ARIMA and Prophet converge: both project a {direction_a} "
                f"(ARIMA {a_pct:+.1f}%, Prophet {p_pct:+.1f}%), raising forecast confidence."
            )
        elif agreement == "diverging":
            narrative = (
                f"Models diverge: ARIMA projects {a_pct:+.1f}% ({direction_a}) "
                f"while Prophet projects {p_pct:+.1f}% ({direction_p}) — high uncertainty, "
                f"treat the ensemble with caution."
            )
        else:
            narrative = (
                f"ARIMA projects {a_pct:+.1f}% versus Prophet's {p_pct:+.1f}%; "
                f"moderate agreement — ensemble average used as headline forecast."
            )

        return agreement, narrative

    def _narrative(self, r: TechnicalResult) -> str:
        parts = []

        if r.trend_direction == "uptrend":
            parts.append(f"{r.symbol} is in an uptrend")
        elif r.trend_direction == "downtrend":
            parts.append(f"{r.symbol} is in a downtrend")
        else:
            parts.append(f"{r.symbol} is trading sideways")

        if r.rsi_14 is not None:
            if r.rsi_14 > 70:
                parts.append(f"RSI at {r.rsi_14:.1f} signals overbought conditions")
            elif r.rsi_14 < 30:
                parts.append(f"RSI at {r.rsi_14:.1f} signals oversold conditions")
            else:
                parts.append(f"RSI at {r.rsi_14:.1f} is neutral")

        if r.forecast and r.forecast.ensemble_prices:
            last = r.forecast.ensemble_prices[-1]
            if last is not None and r.current_price:
                pct = (last - r.current_price) / r.current_price * 100
                parts.append(
                    f"5-day ensemble forecast: ${last:.2f} ({pct:+.1f}% from current ${r.current_price:.2f})"
                )

        return "; ".join(parts) + "."

    def _price_history(self, close: pd.Series) -> list[dict]:
        history_len = min(len(close), CHART_HISTORY_DAYS)
        if history_len == 0:
            return []

        sma_20_series = (
            ta.trend.SMAIndicator(close, window=20).sma_indicator() if len(close) >= 20 else None
        )
        sma_50_series = (
            ta.trend.SMAIndicator(close, window=50).sma_indicator() if len(close) >= 50 else None
        )

        bb_upper_series = bb_lower_series = None
        if len(close) >= 20:
            bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
            bb_upper_series = bb.bollinger_hband()
            bb_lower_series = bb.bollinger_lband()

        rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi() if len(close) >= 14 else None

        macd_line_series = macd_signal_series = macd_hist_series = None
        if len(close) >= 26:
            macd_ind = ta.trend.MACD(close)
            macd_line_series = macd_ind.macd()
            macd_signal_series = macd_ind.macd_signal()
            macd_hist_series = macd_ind.macd_diff()

        history = []
        for date, price in close.tail(history_len).items():
            history.append({
                "date": date.strftime("%Y-%m-%d"),
                "close": safe_float(price),
                "sma_20": safe_float(sma_20_series.loc[date]) if sma_20_series is not None else None,
                "sma_50": safe_float(sma_50_series.loc[date]) if sma_50_series is not None else None,
                "bb_upper": safe_float(bb_upper_series.loc[date]) if bb_upper_series is not None else None,
                "bb_lower": safe_float(bb_lower_series.loc[date]) if bb_lower_series is not None else None,
                "rsi": safe_float(rsi_series.loc[date]) if rsi_series is not None else None,
                "macd": safe_float(macd_line_series.loc[date]) if macd_line_series is not None else None,
                "macd_signal": safe_float(macd_signal_series.loc[date]) if macd_signal_series is not None else None,
                "macd_hist": safe_float(macd_hist_series.loc[date]) if macd_hist_series is not None else None,
            })
        return history

    def _compute_probability(self, r: TechnicalResult) -> ProbabilityResult:
        components: dict[str, float] = {}

        # Trend direction
        components["trend"] = {"uptrend": 1.0, "downtrend": -1.0}.get(r.trend_direction, 0.0)

        # RSI mean-reversion: oversold (<30) is bullish, overbought (>70) is bearish
        if r.rsi_14 is not None:
            components["rsi"] = max(-1.0, min(1.0, (50.0 - r.rsi_14) / 25.0))
        else:
            components["rsi"] = 0.0

        # MACD histogram, normalized by ~0.5% of price
        if r.macd_histogram is not None and r.current_price:
            normalizer = abs(r.current_price) * 0.005
            components["macd"] = max(-1.0, min(1.0, r.macd_histogram / normalizer)) if normalizer else 0.0
        else:
            components["macd"] = 0.0

        # SMA stack alignment
        if r.current_price is not None and r.sma_20 is not None and r.sma_50 is not None:
            if r.current_price > r.sma_20 > r.sma_50:
                components["sma_stack"] = 1.0
            elif r.current_price < r.sma_20 < r.sma_50:
                components["sma_stack"] = -1.0
            elif r.current_price > r.sma_20:
                components["sma_stack"] = 0.5
            elif r.current_price < r.sma_20:
                components["sma_stack"] = -0.5
            else:
                components["sma_stack"] = 0.0
        else:
            components["sma_stack"] = 0.0

        # Bollinger %B: near lower band (low %B) is bullish, near upper band is bearish
        if r.bb_pct is not None:
            components["bollinger"] = max(-1.0, min(1.0, (0.5 - r.bb_pct) * 2))
        else:
            components["bollinger"] = 0.0

        # 5-day ensemble forecast direction, scaled by model agreement
        forecast_score = 0.0
        if r.forecast and r.forecast.ensemble_prices and r.current_price:
            last = r.forecast.ensemble_prices[-1]
            if last is not None:
                pct_change = (last - r.current_price) / r.current_price
                forecast_score = max(-1.0, min(1.0, pct_change / 0.05))
                agreement_multiplier = {
                    "converging": 1.0, "neutral": 0.7, "single_model": 0.6, "diverging": 0.4,
                }.get(r.forecast.agreement, 0.5)
                forecast_score *= agreement_multiplier
        components["forecast"] = forecast_score

        total_weight = sum(PROBABILITY_WEIGHTS.values())
        weighted_sum = sum(components[k] * w for k, w in PROBABILITY_WEIGHTS.items())
        score = weighted_sum / total_weight if total_weight else 0.0

        # Dampen confidence toward neutral when ADX signals a weak/absent trend
        if r.adx is not None and r.adx < 20:
            score *= 0.6

        bullish = max(5.0, min(95.0, round(50.0 + score * 45.0, 1)))
        bearish = round(100.0 - bullish, 1)

        weighted_components = {k: round(components[k] * PROBABILITY_WEIGHTS[k], 3) for k in PROBABILITY_WEIGHTS}
        top = sorted(weighted_components.items(), key=lambda kv: abs(kv[1]), reverse=True)[:2]
        labels = {
            "trend": "trend direction", "rsi": "RSI", "macd": "MACD momentum",
            "sma_stack": "SMA alignment", "bollinger": "Bollinger %B", "forecast": "5-day forecast",
        }
        driver_strs = []
        for name, val in top:
            if abs(val) < 1e-6:
                continue
            direction = "bullish" if val > 0 else "bearish"
            driver_strs.append(f"{labels.get(name, name)} ({direction})")

        direction_word = "bullish" if bullish > 55 else "bearish" if bullish < 45 else "neutral"
        drivers_text = " and ".join(driver_strs) if driver_strs else "mixed signals with no dominant driver"
        summary = (
            f"{r.symbol}: {bullish:.0f}% bullish / {bearish:.0f}% bearish over the next 5 days "
            f"({direction_word} lean), driven mainly by {drivers_text}."
        )

        return ProbabilityResult(
            bullish_probability=bullish,
            bearish_probability=bearish,
            components=weighted_components,
            summary=summary,
        )

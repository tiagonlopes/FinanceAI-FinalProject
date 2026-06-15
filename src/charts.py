import logging

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

logger = logging.getLogger("stock_analyzer.charts")


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _next_business_days(start_date_str: str, n: int) -> list[str]:
    start = pd.to_datetime(start_date_str)
    bdays = pd.bdate_range(start=start + pd.Timedelta(days=1), periods=n)
    return [d.strftime("%Y-%m-%d") for d in bdays]


def build_market_overview_chart(analysis_json: dict) -> str:
    fund_map = {f["symbol"]: f for f in analysis_json.get("fundamental", [])}

    fig = go.Figure()
    for tech in analysis_json.get("technical", []):
        history = tech.get("price_history") or []
        if not history:
            continue
        base = history[0].get("close")
        if not base:
            continue

        dates = [h["date"] for h in history]
        rel = [(h["close"] / base - 1) * 100 if h.get("close") is not None else None for h in history]
        is_bench = fund_map.get(tech["symbol"], {}).get("is_benchmark", False)

        fig.add_trace(go.Scatter(
            x=dates, y=rel, name=tech["symbol"],
            mode="lines",
            line=dict(dash="dot" if is_bench else "solid", width=1.5 if is_bench else 2.5),
        ))

    if not fig.data:
        return ""

    fig.update_layout(
        title=dict(text="Relative Performance — Stocks vs. Benchmarks (normalized %)", y=0.97, yanchor="top"),
        yaxis_title="Change (%)",
        template="plotly_white",
        height=520,
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
        margin=dict(t=60, b=80, l=60, r=20),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def build_price_forecast_chart(tech: dict) -> str:
    history = tech.get("price_history") or []
    if not history:
        return ""

    dates = [h["date"] for h in history]
    closes = [h.get("close") for h in history]
    sma20 = [h.get("sma_20") for h in history]
    sma50 = [h.get("sma_50") for h in history]
    bb_upper = [h.get("bb_upper") for h in history]
    bb_lower = [h.get("bb_lower") for h in history]

    fig = go.Figure()
    if any(v is not None for v in bb_upper) and any(v is not None for v in bb_lower):
        fig.add_trace(go.Scatter(x=dates, y=bb_upper, line=dict(width=0), showlegend=False,
                                  hoverinfo="skip", name="BB upper"))
        fig.add_trace(go.Scatter(x=dates, y=bb_lower, line=dict(width=0), fill="tonexty",
                                  fillcolor="rgba(127,140,141,0.12)",
                                  name="Bollinger Bands (20, 2σ)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=dates, y=closes, name="Close", mode="lines",
                              line=dict(color="#1f6dad", width=2)))
    if any(v is not None for v in sma20):
        fig.add_trace(go.Scatter(x=dates, y=sma20, name="SMA 20", mode="lines",
                                  line=dict(color="#e67e22", dash="dot")))
    if any(v is not None for v in sma50):
        fig.add_trace(go.Scatter(x=dates, y=sma50, name="SMA 50", mode="lines",
                                  line=dict(color="#27ae60", dash="dot")))

    forecast = tech.get("forecast") or {}
    anchor_date = dates[-1]
    anchor_price = closes[-1]

    model_styles = {"arima": ("ARIMA", "#e74c3c"), "prophet": ("Prophet", "#8e44ad")}
    for key, (label, color) in model_styles.items():
        m = forecast.get(key)
        if not m or not m.get("prices"):
            continue
        n = len(m["prices"])
        fdates = _next_business_days(anchor_date, n)
        x = [anchor_date] + fdates
        upper = [anchor_price] + m.get("upper", [None] * n)
        lower = [anchor_price] + m.get("lower", [None] * n)
        prices = [anchor_price] + m["prices"]

        fig.add_trace(go.Scatter(x=x, y=upper, line=dict(width=0), showlegend=False,
                                  hoverinfo="skip", name=f"{label} upper"))
        fig.add_trace(go.Scatter(x=x, y=lower, line=dict(width=0), fill="tonexty",
                                  fillcolor=_hex_to_rgba(color, 0.15),
                                  name=f"{label} 80% CI", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=x, y=prices, name=f"{label} forecast", mode="lines+markers",
                                  line=dict(color=color, dash="dash")))

    ensemble = forecast.get("ensemble_prices") or []
    if ensemble and anchor_price is not None:
        fdates = _next_business_days(anchor_date, len(ensemble))
        fig.add_trace(go.Scatter(
            x=[anchor_date] + fdates, y=[anchor_price] + ensemble,
            name="Ensemble forecast", mode="lines+markers",
            line=dict(color="#2c3e50", width=3),
        ))

    fig.update_layout(
        title=dict(text=f"{tech.get('symbol', '')} — Price History & 5-Day Forecast", y=0.97, yanchor="top"),
        template="plotly_white",
        height=460,
        yaxis_title="Price ($)",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
        margin=dict(t=60, b=80, l=60, r=20),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def build_rsi_chart(tech: dict) -> str:
    history = tech.get("price_history") or []
    if not history or not any(h.get("rsi") is not None for h in history):
        return ""

    dates = [h["date"] for h in history]
    rsi = [h.get("rsi") for h in history]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=rsi, name="RSI (14)", mode="lines",
                              line=dict(color="#8e44ad", width=2)))
    fig.add_hline(y=70, line=dict(color="#e74c3c", dash="dash", width=1))
    fig.add_hline(y=30, line=dict(color="#27ae60", dash="dash", width=1))

    fig.update_layout(
        title=dict(text=f"{tech.get('symbol', '')} — RSI (14)", y=0.95, yanchor="top"),
        template="plotly_white",
        height=260,
        yaxis=dict(title="RSI", range=[0, 100]),
        showlegend=False,
        margin=dict(t=50, b=30, l=60, r=20),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def build_macd_chart(tech: dict) -> str:
    history = tech.get("price_history") or []
    if not history or not any(h.get("macd") is not None for h in history):
        return ""

    dates = [h["date"] for h in history]
    macd = [h.get("macd") for h in history]
    signal = [h.get("macd_signal") for h in history]
    hist = [h.get("macd_hist") for h in history]

    hist_colors = ["#27ae60" if (v is not None and v >= 0) else "#e74c3c" for v in hist]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=dates, y=hist, name="Histogram", marker_color=hist_colors, opacity=0.5))
    fig.add_trace(go.Scatter(x=dates, y=macd, name="MACD", mode="lines",
                              line=dict(color="#1f6dad", width=2)))
    fig.add_trace(go.Scatter(x=dates, y=signal, name="Signal", mode="lines",
                              line=dict(color="#e67e22", width=1.5, dash="dot")))

    fig.update_layout(
        title=dict(text=f"{tech.get('symbol', '')} — MACD (12, 26, 9)", y=0.95, yanchor="top"),
        template="plotly_white",
        height=280,
        yaxis_title="MACD",
        legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="left", x=0),
        margin=dict(t=50, b=60, l=60, r=20),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def build_probability_gauge(tech: dict) -> str:
    prob = tech.get("probability") or {}
    bullish = prob.get("bullish_probability")
    if bullish is None:
        return ""

    color = "#27ae60" if bullish >= 55 else "#e74c3c" if bullish <= 45 else "#f39c12"
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=bullish,
        number={"suffix": "%"},
        title={"text": f"{tech.get('symbol', '')} — Bullish Probability (5-day, technical)", "font": {"size": 14}},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 45], "color": "#fdecea"},
                {"range": [45, 55], "color": "#fef5e7"},
                {"range": [55, 100], "color": "#eafaf1"},
            ],
        },
    ))
    fig.update_layout(height=260, margin=dict(t=70, b=10, l=30, r=30))
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def build_charts_section(analysis_json: dict) -> str:
    tech_list = analysis_json.get("technical", [])
    fund_map = {f["symbol"]: f for f in analysis_json.get("fundamental", [])}

    parts = ['<h2>Charts &amp; Probability Analysis</h2>']

    overview = build_market_overview_chart(analysis_json)
    if overview:
        parts.append("<h3>Relative Performance Overview</h3>")
        parts.append(f'<div class="chart-container">{overview}</div>')

    stocks = [t for t in tech_list if not fund_map.get(t["symbol"], {}).get("is_benchmark", False)]
    benchmarks = [t for t in tech_list if fund_map.get(t["symbol"], {}).get("is_benchmark", False)]

    for group_title, group in [("Stocks", stocks), ("Market Benchmarks", benchmarks)]:
        if not group:
            continue
        parts.append(f"<h3>{group_title}</h3>")
        for tech in group:
            parts.append(f'<h4>{tech.get("symbol", "")}</h4>')

            chart = build_price_forecast_chart(tech)
            if chart:
                parts.append(f'<div class="chart-container">{chart}</div>')

            rsi_chart = build_rsi_chart(tech)
            macd_chart = build_macd_chart(tech)
            if rsi_chart or macd_chart:
                parts.append('<div class="chart-row">')
                if rsi_chart:
                    parts.append(f'<div class="chart-container chart-half">{rsi_chart}</div>')
                if macd_chart:
                    parts.append(f'<div class="chart-container chart-half">{macd_chart}</div>')
                parts.append('</div>')

            gauge = build_probability_gauge(tech)
            if gauge:
                parts.append(f'<div class="chart-container chart-gauge">{gauge}</div>')

            prob = tech.get("probability") or {}
            if prob.get("summary"):
                parts.append(f'<p class="prob-summary"><em>{prob["summary"]}</em></p>')

    return "\n".join(parts)

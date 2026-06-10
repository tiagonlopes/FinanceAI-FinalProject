import json
import logging
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd


class StockJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.floating):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return float(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )
    # Silence noisy third-party loggers
    for noisy in ("prophet", "cmdstanpy", "urllib3", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("stock_analyzer")


def save_intermediate(data: dict, filename: str, data_dir: str = "data") -> str:
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, filename)
    with open(path, "w") as f:
        json.dump(data, f, cls=StockJSONEncoder, indent=2)
    return path


def load_intermediate(filename: str, data_dir: str = "data") -> dict | None:
    path = os.path.join(data_dir, filename)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None

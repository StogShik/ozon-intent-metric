"""Общие утилиты для validation-скриптов (07-11)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parents[3]
DAILY = ROOT / "data" / "daily_summaries"
OUT_DIR = ROOT / "src" / "metric" / "analysis" / "out"

# 16 фичей production Health Score (из configs/weights.yaml)
GROUPS: dict[str, dict] = {
    "quality": {
        "weight": 0.40,
        "features": {
            "dead_search_rate": -1,
            "bounce_rate": -1,
            "km_median_time_to_cart_s": -1,
        },
    },
    "engagement": {
        "weight": 0.35,
        "features": {
            "rate_clicks": +1,
            "rate_views": +1,
            "rate_favs": +1,
            "multi_category_rate": +1,
            "click_per_view": +1,
            "cart_per_click": +1,
            "cart_rate": +1,
            "session_conversion_rate": +1,
            "mean_duration_s": +1,
            "search_refinement_rate": +1,
        },
    },
    "discovery": {
        "weight": 0.25,
        "features": {
            "rate_unique_queries": +1,
            "rate_searches": +1,
            "view_per_search": +1,
        },
    },
}

ALL_FEATURES = [f for g in GROUPS.values() for f in g["features"]]

# anti-фичи (direction == -1): аномалия — рост; для positive — падение
ANTI = {f for g in GROUPS.values() for f, d in g["features"].items() if d == -1}


def load_daily() -> pd.DataFrame:
    """Aggregate day_summary across categories (session-weighted)."""
    df = pl.read_parquet(str(DAILY / "*.parquet"))
    cols = [c for c in df.columns if c not in ("date", "category")]
    weighted = [(pl.col(c) * pl.col("n_sessions")).sum().alias(f"_w_{c}") for c in cols]
    denom = [
        pl.when(pl.col(c).is_not_null())
        .then(pl.col("n_sessions"))
        .otherwise(pl.lit(0, dtype=pl.UInt32))
        .sum()
        .alias(f"_d_{c}")
        for c in cols
    ]
    d = (
        df.group_by("date")
        .agg([pl.col("n_sessions").sum().alias("n_sessions"), *weighted, *denom])
        .with_columns(
            [
                pl.when(pl.col(f"_d_{c}") > 0)
                .then(pl.col(f"_w_{c}") / pl.col(f"_d_{c}"))
                .otherwise(None)
                .alias(c)
                for c in cols
            ]
        )
        .drop([f"_w_{c}" for c in cols] + [f"_d_{c}" for c in cols])
        .sort("date")
        .to_pandas()
    )
    d["date"] = pd.to_datetime(d["date"])
    return d


def index_metric(today: float, base: float, invert: bool) -> float:
    if base is None or np.isnan(base) or base == 0 or today is None or np.isnan(today):
        return np.nan
    idx = (today / base) * 100
    return (200 - idx) if invert else idx


def health_score_row(today: dict, baseline: dict) -> dict:
    """Production Health Score: Equal внутри + 0.40/0.35/0.25 между."""
    group_scores = {}
    for g, cfg in GROUPS.items():
        vals = []
        for f, d in cfg["features"].items():
            invert = d == -1
            idx = index_metric(today.get(f, np.nan), baseline.get(f, np.nan), invert)
            if not np.isnan(idx):
                vals.append(idx)
        group_scores[g] = float(np.mean(vals)) if vals else np.nan

    total_w = sum(cfg["weight"] for g, cfg in GROUPS.items() if not np.isnan(group_scores[g]))
    if total_w == 0:
        hs = np.nan
    else:
        hs = sum(
            group_scores[g] * cfg["weight"]
            for g, cfg in GROUPS.items()
            if not np.isnan(group_scores[g])
        ) / total_w
    return {"health": hs, **group_scores}


def mad_threshold(series: np.ndarray, k: float = 3.0) -> tuple[float, float, float]:
    """median ± k·1.4826·MAD."""
    s = np.asarray(series, dtype=float)
    s = s[~np.isnan(s)]
    med = float(np.median(s))
    mad = float(np.median(np.abs(s - med)))
    sigma = 1.4826 * mad
    return med - k * sigma, med, med + k * sigma

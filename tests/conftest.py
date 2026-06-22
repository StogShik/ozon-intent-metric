from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "pipeline"))

from metric.metric import FeatureSpec, GroupSpec, WeightsConfig


FEATURES = ("dead_search_rate", "cart_rate", "rate_views")


@pytest.fixture
def weights() -> WeightsConfig:
    return WeightsConfig(groups=(
        GroupSpec(
            name="quality", weight=0.6, method="equal",
            features=(
                FeatureSpec(name="dead_search_rate", weight=1.0, direction=-1),
                FeatureSpec(name="cart_rate", weight=1.0, direction=+1),
            ),
        ),
        GroupSpec(
            name="engagement", weight=0.4, method="equal",
            features=(FeatureSpec(name="rate_views", weight=1.0, direction=+1),),
        ),
    ))


def make_day_summary(dates, cat_specs) -> pd.DataFrame:
    rows = []
    for d in dates:
        for cat, spec in cat_specs.items():
            n = spec["n_sessions"]
            rows.append({
                "date": pd.Timestamp(d),
                "category": cat,
                "n_users": n,
                "n_sessions": n,
                "is_cart": int(round(spec.get("conv", 0.4) * n)),
                "dead_search_rate": spec["dead_search_rate"],
                "cart_rate": spec["cart_rate"],
                "rate_views": spec["rate_views"],
            })
    return pd.DataFrame(rows)


BASE_SPECS = {
    "A": {"n_sessions": 1000, "conv": 0.5, "dead_search_rate": 0.20,
          "cart_rate": 1.00, "rate_views": 20.0},
    "B": {"n_sessions": 500, "conv": 0.3, "dead_search_rate": 0.40,
          "cart_rate": 0.60, "rate_views": 10.0},
}


@pytest.fixture
def baseline_df() -> pd.DataFrame:
    dates = pd.date_range("2024-03-01", periods=7, freq="D")
    return make_day_summary(dates, BASE_SPECS)


@pytest.fixture
def today_df() -> pd.DataFrame:
    specs = {
        "A": {**BASE_SPECS["A"], "dead_search_rate": 0.26,
              "cart_rate": 0.90, "rate_views": 17.0},
        "B": {**BASE_SPECS["B"], "dead_search_rate": 0.48,
              "cart_rate": 0.54, "rate_views": 9.0},
    }
    return make_day_summary([pd.Timestamp("2024-03-08")], specs)


def make_sessions(n_per_block: int = 100) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    blocks = [
        ("2024-03-01", "web_search_bar", 0.30),
        ("2024-03-01", "mobile", 0.60),
        ("2024-03-02", "web_search_bar", 0.10),
        ("2024-03-02", "mobile", 0.60),
    ]
    rows = []
    sid = 0
    for day, widget, sr in blocks:
        n_success = int(round(sr * n_per_block))
        for i in range(n_per_block):
            rows.append({
                "user_id": 1000 + sid,
                "session_idx": sid,
                "ts_end": pd.Timestamp(day) + pd.Timedelta(hours=int(rng.integers(0, 24))),
                "first_widget": widget,
                "reached_cart": i < n_success,
                "n_unique_queries": int(rng.integers(1, 6)),
                "n_view": int(rng.integers(0, 30)),
                "duration_s": int(rng.integers(5, 600)),
                "first_query": f"запрос {sid}",
            })
            sid += 1
    return pd.DataFrame(rows)


@pytest.fixture
def sessions_df() -> pd.DataFrame:
    return make_sessions()

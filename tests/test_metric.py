from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from conftest import BASE_SPECS, make_day_summary
from metric.metric import (
    compute_metrics,
    health_score,
    index_metric,
    metric_consensus,
    stratified_health,
)


def test_index_metric_positive_direction():
    assert index_metric(100.0, 100.0, +1) == pytest.approx(100.0)
    assert index_metric(110.0, 100.0, +1) == pytest.approx(110.0)
    assert index_metric(90.0, 100.0, +1) == pytest.approx(90.0)


def test_index_metric_anti_direction_symmetry():
    assert index_metric(0.22, 0.20, -1) == pytest.approx(90.0)
    assert index_metric(0.18, 0.20, -1) == pytest.approx(110.0)
    assert index_metric(0.20, 0.20, -1) == pytest.approx(100.0)


def test_index_metric_nan_cases():
    assert math.isnan(index_metric(None, 100.0, +1))
    assert math.isnan(index_metric(100.0, None, +1))
    assert math.isnan(index_metric(100.0, 0.0, +1))
    assert math.isnan(index_metric(float("nan"), 100.0, +1))


def test_consensus_equal_within_domain_between(weights):
    base = {"dead_search_rate": 0.20, "cart_rate": 1.00, "rate_views": 20.0}
    today = {"dead_search_rate": 0.20, "cart_rate": 1.10, "rate_views": 20.0}
    res = metric_consensus(today, base, weights)
    assert res.groups["quality"] == pytest.approx(105.0)
    assert res.groups["engagement"] == pytest.approx(100.0)
    assert res.health == pytest.approx(0.6 * 105 + 0.4 * 100)


def test_consensus_nan_feature_shrinks_group_divisor(weights):
    base = {"dead_search_rate": 0.20, "cart_rate": 1.00, "rate_views": 20.0}
    today = {"dead_search_rate": 0.24, "cart_rate": float("nan"), "rate_views": 20.0}
    res = metric_consensus(today, base, weights)
    assert res.groups["quality"] == pytest.approx(80.0)
    assert math.isnan(res.features["cart_rate"])
    assert res.health == pytest.approx(0.6 * 80 + 0.4 * 100)


def test_consensus_nan_group_renormalizes_weights(weights):
    base = {"dead_search_rate": 0.20, "cart_rate": 1.00, "rate_views": 20.0}
    today = {"dead_search_rate": 0.20, "cart_rate": 1.00, "rate_views": float("nan")}
    res = metric_consensus(today, base, weights)
    assert math.isnan(res.groups["engagement"])
    assert res.health == pytest.approx(res.groups["quality"])


def test_consensus_baseline_equals_today_is_100(weights):
    base = {"dead_search_rate": 0.20, "cart_rate": 1.00, "rate_views": 20.0}
    res = metric_consensus(dict(base), base, weights)
    assert res.health == pytest.approx(100.0)


def test_compute_metrics_session_weighted():
    df = make_day_summary([pd.Timestamp("2024-03-01")], BASE_SPECS)
    m = compute_metrics(df)
    assert m["cart_rate"] == pytest.approx((1000 * 1.0 + 500 * 0.6) / 1500)
    assert m["n_sessions"] == 1500
    assert m["session_conversion_rate"] == pytest.approx(650 / 1500)


def test_compute_metrics_empty_returns_empty():
    assert compute_metrics(pd.DataFrame()) == {}
    assert compute_metrics(None) == {}


def test_stratified_neutralizes_composition_drift(weights, baseline_df):
    drifted = {
        "A": {**BASE_SPECS["A"], "n_sessions": 400},
        "B": {**BASE_SPECS["B"], "n_sessions": 600},
    }
    today = make_day_summary([pd.Timestamp("2024-03-08")], drifted)
    res = health_score(today, baseline_df, weights)
    assert res.stratified == pytest.approx(100.0, abs=1e-9)
    assert abs(res.health - 100.0) > 1.0
    assert res.drift_signal == pytest.approx(res.stratified - res.health)


def test_stratified_skips_low_volume_categories(weights, baseline_df):
    specs = {
        "A": BASE_SPECS["A"],
        "B": {**BASE_SPECS["B"], "n_sessions": 10},
    }
    today = make_day_summary([pd.Timestamp("2024-03-08")], specs)
    scalar, records = stratified_health(
        today, baseline_df, weights, min_sessions_per_cat=100, include_records=True,
    )
    by_cat = {r["category"]: r for r in records}
    assert by_cat["B"]["included"] is False
    assert by_cat["B"]["skip_reason"] == "low_volume"
    assert by_cat["A"]["included"] is True
    assert scalar == pytest.approx(by_cat["A"]["health"])


def test_health_score_drop_day_is_below_100(weights, baseline_df, today_df):
    res = health_score(today_df, baseline_df, weights)
    assert res.health < 100.0
    assert not np.isnan(res.stratified)

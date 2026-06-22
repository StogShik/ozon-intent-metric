from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from conftest import BASE_SPECS, make_day_summary
from metric.metric import health_score
from metric.rca import (
    decompose_health,
    example_failures,
    explain_drop,
    precompute_all_segments,
    segment_breakdown,
    segment_day_view,
    top_contributors,
    weak_spots,
)


def test_decompose_additive_invariants(weights, baseline_df, today_df):
    res = health_score(today_df, baseline_df, weights)
    decomp = decompose_health(today_df, baseline_df, weights, day="2024-03-08")

    feat_sum = decomp[decomp["level"] == "feature"]["contribution"].sum(skipna=True)
    group_sum = decomp[decomp["level"] == "group"]["contribution"].sum(skipna=True)
    cat_mask = (decomp["level"] == "category") & (decomp["included"] == True)
    cat_sum = decomp[cat_mask]["contribution"].sum(skipna=True)

    assert feat_sum == pytest.approx(res.health, abs=1e-6)
    assert group_sum == pytest.approx(res.health, abs=1e-6)
    assert cat_sum == pytest.approx(res.stratified, abs=1e-6)


def test_decompose_row_counts(weights, baseline_df, today_df):
    decomp = decompose_health(today_df, baseline_df, weights, day="2024-03-08")
    assert (decomp["level"] == "group").sum() == 2
    assert (decomp["level"] == "feature").sum() == 3
    assert (decomp["level"] == "category").sum() == 2
    assert (decomp["date"] == "2024-03-08").all()


def test_decompose_nan_feature_kept_as_row(weights, baseline_df, today_df):
    today = today_df.assign(rate_views=np.nan)
    res = health_score(today, baseline_df, weights)
    decomp = decompose_health(today, baseline_df, weights, day="2024-03-08")

    row = decomp[(decomp["level"] == "feature") & (decomp["feature"] == "rate_views")]
    assert len(row) == 1
    assert math.isnan(row["contribution"].iloc[0])
    feat_sum = decomp[decomp["level"] == "feature"]["contribution"].sum(skipna=True)
    assert feat_sum == pytest.approx(res.health, abs=1e-6)


def test_explain_drop_delta_equals_health_diff(weights, baseline_df, today_df):
    good = make_day_summary([pd.Timestamp("2024-03-07")], BASE_SPECS)
    h_good = health_score(good, baseline_df, weights).health
    h_bad = health_score(today_df, baseline_df, weights).health

    decomp = pd.concat([
        decompose_health(good, baseline_df, weights, day="2024-03-07"),
        decompose_health(today_df, baseline_df, weights, day="2024-03-08"),
    ], ignore_index=True)
    result = explain_drop(decomp, "2024-03-07", "2024-03-08")

    assert result["delta_health_raw"] == pytest.approx(h_bad - h_good, abs=1e-6)
    feat_delta_sum = sum(
        r["delta_contribution"] for r in result["by_feature_top"]
        if r["delta_contribution"] is not None
    )
    assert feat_delta_sum == pytest.approx(h_bad - h_good, abs=1e-6)
    deltas = [r["delta_contribution"] for r in result["by_feature_top"]]
    assert deltas == sorted(deltas)


def test_explain_drop_missing_date_returns_caveat(weights, baseline_df, today_df):
    decomp = decompose_health(today_df, baseline_df, weights, day="2024-03-08")
    result = explain_drop(decomp, "2024-03-07", "2024-03-08")
    assert math.isnan(result["delta_health_raw"])
    assert result["caveats"]


def test_top_contributors_worst_first(weights, baseline_df, today_df):
    decomp = decompose_health(today_df, baseline_df, weights, day="2024-03-08")
    top = top_contributors(decomp, "2024-03-08", level="feature", n=3)
    contribs = [r["contribution"] for r in top]
    assert contribs == sorted(contribs)


def test_segment_breakdown_period_aggregate(sessions_df):
    out = segment_breakdown(sessions_df, "first_widget")
    assert (out["date"] == "ALL").all()
    web = out[out["value"] == "web_search_bar"].iloc[0]
    assert web["n_sessions"] == 200
    assert web["success_rate"] == pytest.approx(0.20)
    assert web["lift_vs_overall"] == pytest.approx(0.20 - 0.40)
    assert web["failure_volume"] == 160
    assert out["share"].sum() == pytest.approx(1.0)


def test_segment_breakdown_by_date(sessions_df):
    out = segment_breakdown(sessions_df, "first_widget", by_date=True)
    assert set(out["date"]) == {"2024-03-01", "2024-03-02"}
    d2 = out[(out["date"] == "2024-03-02") & (out["value"] == "web_search_bar")].iloc[0]
    assert d2["n_sessions"] == 100
    assert d2["success_rate"] == pytest.approx(0.10)
    assert d2["lift_vs_overall"] == pytest.approx(0.10 - 0.35)


def test_precompute_includes_both_granularities(sessions_df):
    out = precompute_all_segments(sessions_df, include_daily=True)
    assert (out["date"] == "ALL").any()
    assert (out["date"] != "ALL").any()
    assert "first_widget" in set(out["segment"])


def test_weak_spots_uses_only_period_rows(sessions_df):
    breakdown = precompute_all_segments(sessions_df, include_daily=True)
    out = weak_spots(breakdown, min_volume=50, min_lift_threshold=-0.05, top_n=50)
    assert len(out) > 0
    assert (out["date"] == "ALL").all()


def test_weak_spots_thresholds(sessions_df):
    breakdown = segment_breakdown(sessions_df, "first_widget")
    out = weak_spots(breakdown, min_volume=50, min_lift_threshold=-0.05)
    assert list(out["value"]) == ["web_search_bar"]
    assert weak_spots(breakdown, min_volume=10_000).empty


def test_segment_day_view_baseline_norm(sessions_df):
    breakdown = precompute_all_segments(sessions_df, include_daily=True)
    view = segment_day_view(
        breakdown, "2024-03-02", min_volume=50,
        baseline_period=("2024-03-01", "2024-03-01"),
    )
    assert (view["norm_source"] == "baseline").all()
    web = view[(view["segment"] == "first_widget") & (view["value"] == "web_search_bar")].iloc[0]
    assert web["success_rate_period"] == pytest.approx(0.30)
    assert web["delta_vs_period"] == pytest.approx(-0.20)
    assert web["extra_failures"] == 20
    mob = view[(view["segment"] == "first_widget") & (view["value"] == "mobile")].iloc[0]
    assert mob["extra_failures"] == 0
    assert view.iloc[0]["extra_failures"] == view["extra_failures"].max()


def test_segment_day_view_period_fallback(sessions_df):
    breakdown = precompute_all_segments(sessions_df, include_daily=True)
    view = segment_day_view(breakdown, "2024-03-02", min_volume=50)
    assert (view["norm_source"] == "period").all()
    web = view[(view["segment"] == "first_widget") & (view["value"] == "web_search_bar")].iloc[0]
    assert web["success_rate_period"] == pytest.approx(0.20)
    assert web["extra_failures"] == 10


def test_segment_day_view_handles_legacy_breakdown(sessions_df):
    legacy = segment_breakdown(sessions_df, "first_widget").drop(columns=["date"])
    assert segment_day_view(legacy, "2024-03-02").empty


def test_example_failures_only_failed_and_no_pii(sessions_df):
    examples = example_failures(sessions_df, "first_widget", "web_search_bar", n=10)
    assert 0 < len(examples) <= 10
    for e in examples:
        assert "user_id" not in e
        assert "first_query" in e


def test_example_failures_day_filter(sessions_df):
    examples = example_failures(
        sessions_df, "first_widget", "web_search_bar", n=200, day="2024-03-02",
    )
    assert len(examples) == 90


def test_example_failures_unknown_value_empty(sessions_df):
    assert example_failures(sessions_df, "first_widget", "no_such_widget") == []

"""Smoke-test для metric.py + anomaly.py на 61-дневных данных."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import polars as pl

from anomaly import MadDetector
from metric import (
    DeltaScoreDiagnostic,
    health_score,
    load_weights,
    metric_consensus,
    compute_metrics,
)


ROOT = Path(__file__).resolve().parents[2]
DAILY = ROOT / "data" / "daily_summaries"


def load_daily_summary() -> pd.DataFrame:
    df = pl.read_parquet(str(DAILY / "*.parquet")).to_pandas()
    df["date"] = pd.to_datetime(df["date"])
    return df


def main() -> None:
    df = load_daily_summary()
    print(f"Loaded {len(df):,} rows, {df['date'].nunique()} days, {df['category'].nunique()} categories")

    weights = load_weights()
    print(f"Weights: {[g.name for g in weights.groups]}")

    baseline_end = pd.Timestamp("2024-03-30")
    today_date = pd.Timestamp("2024-04-15")
    baseline_df = df[df["date"] <= baseline_end]
    today_df = df[df["date"] == today_date]
    print(f"Baseline rows: {len(baseline_df)}, Today rows: {len(today_df)}")

    # --- compute_metrics + metric_consensus (dict-API) ---
    today_metrics = compute_metrics(today_df)
    base_metrics = compute_metrics(baseline_df)
    res = metric_consensus(today_metrics, base_metrics, weights)
    print(f"\nmetric_consensus → health={res.health:.2f}")
    for g, gs in res.groups.items():
        print(f"  {g:12s}: {gs:.2f}")

    # --- health_score (DataFrame-API + stratified) ---
    res2 = health_score(today_df, baseline_df, weights, include_stratified=True)
    print(f"\nhealth_score (DataFrame-API):")
    print(f"  raw        = {res2.health:.2f}")
    print(f"  stratified = {res2.stratified:.2f}")
    print(f"  drift      = {res2.drift_signal:+.2f}")

    # --- anomaly detector ---
    print(f"\n--- MadDetector ---")
    # пересоберём baseline по агрегату per day (не per category):
    daily_agg = (
        baseline_df.groupby("date", group_keys=True)
        .apply(lambda g: pd.Series(compute_metrics(g)), include_groups=False)
        .reset_index()
    )
    daily_agg["date"] = pd.to_datetime(daily_agg["date"])
    daily_agg = daily_agg.set_index("date")
    det = MadDetector.fit(daily_agg, weights.feature_directions)
    today_dict = today_metrics
    dow = today_date.dayofweek
    print(f"  today DOW = {dow}, k = {det.k}")
    alerts = []
    for f in weights.all_features:
        if det.check(f, today_dict[f], dow):
            r = det.residual(f, today_dict[f], dow)
            alerts.append((f, today_dict[f], r))
    print(f"  alerts: {len(alerts)}/{len(weights.all_features)}")
    for f, v, r in alerts:
        tag = "DOW" if det.use_dow[f] else "global"
        print(f"    {f:<30}  value={v:.4f}  residual={r:+.4f}  [{tag}]")

    # --- delta diagnostic ---
    print(f"\n--- DeltaScoreDiagnostic (warns!) ---")
    diag = DeltaScoreDiagnostic.fit(baseline_df, weights)
    prev_date = pd.Timestamp("2024-04-14")
    prev_metrics = compute_metrics(df[df["date"] == prev_date])
    delta_s = diag.score(today_metrics, prev_metrics)
    is_anom = diag.is_anomaly(today_metrics, prev_metrics)
    print(f"  Δscore (today − prev) = {delta_s:+.3f}")
    print(f"  threshold = [{diag.threshold_lo:+.3f}, {diag.threshold_hi:+.3f}]")
    print(f"  is_anomaly = {is_anom}")

    print("\n✓ smoke-test OK")


if __name__ == "__main__":
    main()

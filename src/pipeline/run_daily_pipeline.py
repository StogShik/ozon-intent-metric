from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from build_intent_sessions import build_intent_sessions
from metric.anomaly import MadDetector
from metric.metric import compute_metrics, health_score, load_weights
from metric.rca import (
    DECOMP_COLUMNS,
    decompose_health,
    precompute_all_segments,
)


def _safe_div(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    return pl.when(den != 0).then(num / den).otherwise(None)


def _km_median(durations: np.ndarray, events: np.ndarray, min_events: int) -> float | None:
    durations = np.asarray(durations, dtype=float)
    events = np.asarray(events, dtype=bool)
    mask = ~np.isnan(durations)
    durations = durations[mask]
    events = events[mask]
    if int(events.sum()) < min_events or len(durations) == 0:
        return None

    order = np.argsort(durations)
    durations = durations[order]
    events = events[order]
    survival = 1.0
    n_at_risk = len(durations)
    for t in np.unique(durations):
        at_t = durations == t
        event_count = int(events[at_t].sum())
        censored_count = int(at_t.sum()) - event_count
        if event_count:
            survival *= 1.0 - event_count / n_at_risk
            if survival <= 0.5:
                return float(t)
        n_at_risk -= event_count + censored_count
        if n_at_risk <= 0:
            break
    return None


def _km_by_category(day_sessions: pl.DataFrame, min_events: int) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for category in day_sessions["category"].unique().to_list():
        sub = day_sessions.filter(pl.col("category") == category)
        durations = (
            sub.select(
                pl.when(pl.col("reached_cart"))
                .then(pl.col("time_to_cart_s"))
                .otherwise(pl.col("duration_s"))
                .alias("duration")
            )
            .get_column("duration")
            .to_numpy()
        )
        events = sub.get_column("reached_cart").to_numpy()
        rows.append(
            {
                "category": category,
                "km_median_time_to_cart_s": _km_median(durations, events, min_events=min_events),
            }
        )
    return pl.DataFrame(rows, schema={"category": pl.Utf8, "km_median_time_to_cart_s": pl.Float64})


def build_day_summary(
    sessions_path: str | Path,
    output_path: str | Path,
    min_category_sessions: int = 2_000,
    km_min_events: int = 30,
) -> pl.DataFrame:
    """Строит date-category summary для расчета Health Score."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sessions = pl.scan_parquet(str(sessions_path))
    schema = sessions.collect_schema()
    category_expr = (
        pl.col("top_category_in_session")
        if "top_category_in_session" in schema
        else pl.lit(None, dtype=pl.Utf8)
    )

    n_unique_categories_expr = (
        pl.col("n_unique_categories")
        if "n_unique_categories" in schema
        else pl.lit(0, dtype=pl.UInt32)
    )

    base = sessions.with_columns(
        pl.col("ts_end").dt.date().alias("date"),
        category_expr.fill_null("other").alias("_raw_category"),
        n_unique_categories_expr.alias("n_unique_categories"),
    )

    large_categories = (
        base.group_by("_raw_category")
        .agg(pl.len().alias("n_sessions"))
        .filter(pl.col("n_sessions") >= min_category_sessions)
        .collect()
        .get_column("_raw_category")
        .to_list()
    )

    all_dates = (
        base.select("date")
        .unique()
        .sort("date")
        .collect()
        .get_column("date")
        .to_list()
    )

    parts: list[pl.DataFrame] = []
    for current_date in all_dates:
        day_sessions = (
            base.filter(pl.col("date") == current_date)
            .with_columns(
                pl.when(pl.col("_raw_category").is_in(large_categories))
                .then(pl.col("_raw_category"))
                .otherwise(pl.lit("other"))
                .alias("category")
            )
            .collect()
        )

        grouped = (
            day_sessions.lazy()
            .group_by(["date", "category"])
            .agg(
                pl.col("user_id").n_unique().cast(pl.UInt32).alias("n_users"),
                pl.len().cast(pl.UInt32).alias("n_sessions"),
                pl.col("n_events").mean().alias("rate_events"),
                pl.col("n_search").mean().alias("rate_searches"),
                pl.col("n_view").mean().alias("rate_views"),
                pl.col("n_click").mean().alias("rate_clicks"),
                pl.col("n_fav").mean().alias("rate_favs"),
                pl.col("n_unique_queries").mean().alias("rate_unique_queries"),
                pl.col("reached_cart").cast(pl.Int32).sum().alias("is_cart"),
                pl.col("n_cart").mean().alias("cart_rate"),
                pl.col("reached_cart").cast(pl.Float64).mean().alias("session_conversion_rate"),
                (pl.col("n_events") == 1).cast(pl.Float64).mean().alias("bounce_rate"),
                (pl.col("n_click") == 0).cast(pl.Float64).mean().alias("dead_search_rate"),
                (pl.col("n_unique_queries") > 1).cast(pl.Float64).mean().alias("search_refinement_rate"),
                pl.col("duration_s").mean().alias("mean_duration_s"),
                (pl.col("n_unique_categories") > 1).cast(pl.Float64).mean().alias("multi_category_rate"),
                _safe_div(pl.col("n_view").sum(), pl.col("n_search").sum()).alias("view_per_search"),
                _safe_div(pl.col("n_click").sum(), pl.col("n_view").sum()).alias("click_per_view"),
                _safe_div(pl.col("n_cart").sum(), pl.col("n_click").sum()).alias("cart_per_click"),
                pl.col("n_search").filter(pl.col("reached_cart")).mean().alias("mean_searches_to_cart"),
                pl.col("n_unique_queries").filter(pl.col("reached_cart")).mean().alias("mean_unique_queries_to_cart"),
                pl.col("time_to_cart_s").filter(pl.col("reached_cart")).mean().alias("mean_time_to_cart_s"),
            )
            .collect()
        )
        parts.append(grouped.join(_km_by_category(day_sessions, km_min_events), on="category", how="left"))

    result = pl.concat(parts, how="vertical_relaxed").sort(["date", "category"])
    result.write_parquet(output_path)
    return result


def _fit_detector(baseline: pd.DataFrame, weights, k: float) -> MadDetector:
    """Свернуть day×category baseline в day-level DatetimeIndexed, потом fit."""
    import warnings
    daily = (
        baseline.groupby("date", group_keys=True)
        .apply(lambda g: pd.Series(compute_metrics(g)), include_groups=False)
        .reset_index()
        .set_index("date")
        .sort_index()
    )
    daily.index = pd.to_datetime(daily.index)
    directions = {
        f: d for f, d in weights.feature_directions.items()
        if f in daily.columns
    }
    dropped = [f for f in weights.feature_directions if f not in daily.columns]
    if dropped:
        warnings.warn(
            f"MadDetector skipping features not in baseline day_summary: {dropped}. "
            f"Check weights.yaml vs CONTRACT_SUMMARY_OUTPUT.",
            UserWarning,
            stacklevel=2,
        )
    return MadDetector.fit(daily, directions, k=k)


def build_daily_metrics(
    day_summary_path: str | Path,
    output_path: str | Path,
    weights_path: str | Path,
    baseline_start: date | None = None,
    baseline_end: date | None = None,
    baseline_days: int = 30,
    decomposition_out: str | Path | None = None,
    detector_out: str | Path | None = None,
    mad_k: float = 3.5,
    min_sessions_per_cat: int = 100,
) -> pd.DataFrame:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = pl.read_parquet(str(day_summary_path)).to_pandas()
    summary["date"] = pd.to_datetime(summary["date"])
    all_dates = sorted(summary["date"].unique())
    if not all_dates:
        raise ValueError("day summary is empty")

    if baseline_start is None:
        baseline_start_ts = pd.Timestamp(all_dates[0])
    else:
        baseline_start_ts = pd.Timestamp(baseline_start)

    if baseline_end is None:
        baseline_end_ts = baseline_start_ts + pd.Timedelta(days=baseline_days - 1)
    else:
        baseline_end_ts = pd.Timestamp(baseline_end)

    baseline = summary[(summary["date"] >= baseline_start_ts) & (summary["date"] <= baseline_end_ts)]
    if len(baseline) == 0:
        raise ValueError("baseline window has no rows")

    weights = load_weights(weights_path)

    print(f"Fitting MadDetector on {baseline['date'].nunique()} baseline days (k={mad_k})...")
    detector = _fit_detector(baseline, weights, k=mad_k)
    if detector_out is not None:
        detector_path = Path(detector_out)
        detector.save(detector_path)
        print(f"Saved MadDetector state to {detector_path}")

    rows: list[dict[str, object]] = []
    decomp_parts: list[pd.DataFrame] = []

    for current_date in all_dates:
        today = summary[summary["date"] == current_date]
        result = health_score(today, baseline, weights, include_stratified=True)
        day_iso = pd.Timestamp(current_date).date().isoformat()

        decomp = decompose_health(
            today_df=today,
            baseline_df=baseline,
            weights=weights,
            day=day_iso,
            detector=detector,
            min_sessions_per_cat=min_sessions_per_cat,
        )
        decomp_parts.append(decomp)

        sum_feat = decomp[decomp["level"] == "feature"]["contribution"].sum(skipna=True)
        if not np.isnan(result.health) and abs(sum_feat - result.health) > 1e-4:
            raise AssertionError(
                f"decomposition drift (raw): Σ feature_contrib={sum_feat:.6f} "
                f"vs health={result.health:.6f} on {day_iso}"
            )
        cat_mask = (decomp["level"] == "category") & (decomp["included"] == True)
        sum_cat = decomp[cat_mask]["contribution"].sum(skipna=True)
        if not np.isnan(result.stratified) and abs(sum_cat - result.stratified) > 1e-4:
            raise AssertionError(
                f"decomposition drift (stratified): Σ category_contrib={sum_cat:.6f} "
                f"vs stratified={result.stratified:.6f} on {day_iso}"
            )

        feat_rows = decomp[decomp["level"] == "feature"]
        n_alerts = int(feat_rows["is_anomaly"].fillna(False).sum())
        active_alerts = sorted(
            feat_rows[feat_rows["is_anomaly"] == True]["feature"].tolist()
        )

        row: dict[str, object] = {
            "date": day_iso,
            "baseline_start": baseline_start_ts.date().isoformat(),
            "baseline_end": baseline_end_ts.date().isoformat(),
            "health_score": result.health,
            "stratified_health_score": result.stratified,
            "traffic_drift_signal": result.drift_signal,
            "n_sessions": int(today["n_sessions"].sum()),
            "n_users": int(today["n_users"].sum()),
            "converted_sessions": int(today["is_cart"].sum()),
            "mean_searches_to_cart": _weighted(today, "mean_searches_to_cart"),
            "mean_unique_queries_to_cart": _weighted(today, "mean_unique_queries_to_cart"),
            "mean_time_to_cart_s": _weighted(today, "mean_time_to_cart_s"),
            "n_anomalies_active": n_alerts,
            "alert_features": ",".join(active_alerts),
            "has_alert": n_alerts > 0,
        }
        row.update({f"group_{name}": value for name, value in result.groups.items()})
        row.update({f"feature_{name}": value for name, value in result.features.items()})
        rows.append(row)

    metrics = pd.DataFrame(rows)
    metrics.to_parquet(output_path, index=False)

    if decomposition_out is not None and decomp_parts:
        decomp_df = pd.concat(decomp_parts, ignore_index=True)
        decomp_df = decomp_df[list(DECOMP_COLUMNS)]
        decomp_df = decomp_df.sort_values(["date", "level", "group", "feature", "category"],
                                          na_position="last").reset_index(drop=True)
        decomp_path = Path(decomposition_out)
        decomp_path.parent.mkdir(parents=True, exist_ok=True)
        decomp_df.to_parquet(decomp_path, index=False)
        print(f"Saved decomposition ({len(decomp_df):,} rows) to {decomp_path}")

    return metrics


def build_segment_breakdown(
    sessions_path: str | Path,
    output_path: str | Path,
) -> pd.DataFrame:
    """Structural RCA: per-segment success_rate / median метрик из per-session данных.

    Читает intent_sessions_with_query_features.parquet (NLP-обогащённые сессии),
    группирует по DEFAULT_SEGMENTS (NLP-сложность, product-base миссы,
    query_len/n_unique_queries/n_view buckets, top_category, first_widget).
    Пишет две гранулярности: агрегат периода (date="ALL") и per-day строки —
    вторые нужны UI для «какой сегмент просел именно в этот день».
    """
    sessions_path = Path(sessions_path)
    if not sessions_path.exists():
        print(f"WARN: {sessions_path} not found; skipping segment breakdown.")
        return pd.DataFrame()
    print(f"Building segment breakdown from {sessions_path}...")
    sessions = pl.read_parquet(str(sessions_path)).to_pandas()
    out = precompute_all_segments(sessions, include_daily=True)
    if out.empty:
        print("WARN: segment breakdown empty (no segments matched).")
        return out
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(output_path, index=False)
    n_all = int((out["date"] == "ALL").sum())
    print(f"Saved segment breakdown ({n_all} period rows + {len(out) - n_all} daily rows, "
          f"{out['segment'].nunique()} segments) to {output_path}")
    return out


def _weighted(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns:
        return float("nan")
    mask = df[column].notna()
    if not mask.any():
        return float("nan")
    weights = df.loc[mask, "n_sessions"]
    return float((df.loc[mask, column] * weights).sum() / weights.sum())


def run_pipeline(args: argparse.Namespace) -> None:
    sessions_path = Path(args.sessions_out)
    if args.in_sessions:
        sessions_path = Path(args.in_sessions)
    else:
        if not args.start or not args.end:
            raise ValueError("--start and --end are required when --in-sessions is not provided")
        print("Building intent sessions...")
        build_intent_sessions(
            events_root=args.input,
            products_root=args.products,
            start_date=date.fromisoformat(args.start),
            end_date=date.fromisoformat(args.end),
            output_path=sessions_path,
            gap_min=args.gap_min,
            chunk_days=args.session_chunk_days,
        )

    print("Building day summary...")
    build_day_summary(
        sessions_path=sessions_path,
        output_path=args.day_summary_out,
        min_category_sessions=args.min_category_sessions,
        km_min_events=args.km_min_events,
    )

    print("Building daily health metrics + decomposition...")
    metrics = build_daily_metrics(
        day_summary_path=args.day_summary_out,
        output_path=args.daily_metrics_out,
        weights_path=args.weights,
        baseline_start=date.fromisoformat(args.baseline_start) if args.baseline_start else None,
        baseline_end=date.fromisoformat(args.baseline_end) if args.baseline_end else None,
        baseline_days=args.baseline_days,
        decomposition_out=args.decomposition_out,
        detector_out=args.detector_out,
        mad_k=args.mad_k,
        min_sessions_per_cat=args.min_sessions_per_cat,
    )
    print(f"Saved day summary to {args.day_summary_out}")
    print(f"Saved daily metrics to {args.daily_metrics_out}")
    print(metrics[["date", "health_score", "n_anomalies_active", "alert_features"]].head(10).to_string(index=False))

    if args.segments_sessions and args.segments_out:
        build_segment_breakdown(
            sessions_path=args.segments_sessions,
            output_path=args.segments_out,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build daily intent health metrics.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--in-sessions", help="Existing intent_sessions parquet")
    source.add_argument("--start", help="Start date for raw-event build, inclusive: YYYY-MM-DD")
    parser.add_argument("--end", help="End date for raw-event build, inclusive: YYYY-MM-DD")
    parser.add_argument("--input", default="data/user_actions_3_months")
    parser.add_argument("--products", default="data/product_information")
    parser.add_argument("--gap-min", type=int, default=35)
    parser.add_argument("--session-chunk-days", type=int, default=1)
    parser.add_argument("--sessions-out", default="data/intent_sessions.parquet")
    parser.add_argument("--day-summary-out", default="data/day_summary.parquet")
    parser.add_argument("--daily-metrics-out", default="data/daily_metrics.parquet")
    parser.add_argument("--weights", default=str(ROOT / "configs" / "weights.yaml"))
    parser.add_argument("--baseline-start")
    parser.add_argument("--baseline-end")
    parser.add_argument("--baseline-days", type=int, default=30)
    parser.add_argument("--min-category-sessions", type=int, default=2_000)
    parser.add_argument("--km-min-events", type=int, default=30)
    parser.add_argument("--decomposition-out", default="data/decomposition_full.parquet",
                        help="Long-format per-day decomposition for RCA UI. Set to '' to skip.")
    parser.add_argument("--detector-out", default="data/framework_state/mad_detector.json",
                        help="Persisted MadDetector state. Set to '' to skip.")
    parser.add_argument("--mad-k", type=float, default=3.5,
                        help="MAD threshold multiplier (default 3.5).")
    parser.add_argument("--min-sessions-per-cat", type=int, default=100,
                        help="Categories below this skip stratified contribution (default 100).")
    parser.add_argument("--segments-sessions", default="",
                        help="Path to intent_sessions_with_query_features.parquet for weak-spots; empty = skip.")
    parser.add_argument("--segments-out", default="data/segment_breakdown.parquet",
                        help="Output for structural RCA breakdown.")
    args = parser.parse_args()
    if not args.decomposition_out:
        args.decomposition_out = None
    if not args.detector_out:
        args.detector_out = None
    if not args.segments_sessions:
        args.segments_sessions = None
    run_pipeline(args)


if __name__ == "__main__":
    main()

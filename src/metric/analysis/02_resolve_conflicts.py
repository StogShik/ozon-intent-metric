"""
A0: эмпирически разрешить конфликты конфига перед расчётом весов.

Вопросы:
  1. search_refinement_rate: +1 (engagement) или -1 (quality)?
  2. mean_duration_s: включать в Health Score (и в какую группу) или вне?
  3. Группировка фичей — фиксируем 3-групповую структуру из доки.

Метод: Δ-корреляции (день-к-дню) с reference-сигналами:
  cart_rate (главный коммерческий), session_conversion_rate (binary конверсия),
  dead_search_rate (фрустрация), bounce_rate (отказ).

Решение → docs/metric_choice.md §A0
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parents[3]
DAILY = ROOT / "data" / "daily_summaries"


def load_daily() -> pd.DataFrame:
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
    daily = (
        df.group_by("date")
        .agg(
            [
                pl.col("n_sessions").sum().alias("n_sessions"),
                pl.col("is_cart").sum().alias("is_cart_total"),
                *weighted,
                *denom,
            ]
        )
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
    daily["date"] = pd.to_datetime(daily["date"])
    return daily


def main() -> None:
    daily = load_daily()
    print(f"Дней: {len(daily)}  ({daily['date'].min().date()} → {daily['date'].max().date()})")
    print(f"NaN-counts:\n{daily.isna().sum()[daily.isna().sum() > 0]}\n")

    # ---------- A0.1 search_refinement_rate ----------
    print("=" * 70)
    print("A0.1  search_refinement_rate — direction probe")
    print("=" * 70)
    refs = ["cart_rate", "session_conversion_rate", "dead_search_rate", "bounce_rate"]
    rows = []
    for r in refs:
        rows.append(
            {
                "ref": r,
                "corr_level": daily["search_refinement_rate"].corr(daily[r]),
                "corr_delta": daily["search_refinement_rate"].diff().corr(daily[r].diff()),
            }
        )
    out = pd.DataFrame(rows).round(3)
    print(out.to_string(index=False))
    print()

    # ---------- A0.2 mean_duration_s ----------
    print("=" * 70)
    print("A0.2  mean_duration_s — direction & group fit")
    print("=" * 70)
    quality_refs = ["dead_search_rate", "search_refinement_rate", "bounce_rate", "km_median_time_to_cart_s"]
    engagement_refs = ["rate_clicks", "rate_views", "cart_rate", "session_conversion_rate"]
    discovery_refs = ["rate_unique_queries", "rate_searches", "view_per_search"]

    rows = []
    for group_name, refs in [
        ("quality", quality_refs),
        ("engagement", engagement_refs),
        ("discovery", discovery_refs),
    ]:
        ref_dirs = {
            "dead_search_rate": -1,
            "search_refinement_rate": -1,
            "bounce_rate": -1,
            "km_median_time_to_cart_s": -1,
            "rate_clicks": +1,
            "rate_views": +1,
            "cart_rate": +1,
            "session_conversion_rate": +1,
            "rate_unique_queries": +1,
            "rate_searches": +1,
            "view_per_search": +1,
        }
        cs = []
        for r in refs:
            if r not in daily.columns:
                continue
            c = daily["mean_duration_s"].diff().corr(daily[r].diff())
            if c is None or np.isnan(c):
                continue
            cs.append(c * ref_dirs[r])
        avg_oriented = float(np.mean(cs)) if cs else float("nan")
        rows.append(
            {
                "group": group_name,
                "mean_oriented_delta_corr": avg_oriented,
                "n_refs": len(cs),
            }
        )
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    print()
    print("Интерпретация: mean_oriented_delta_corr > 0.3 ⇒ фича вписывается в группу как +1.")
    print("Если ни одна группа не даёт > 0.2 ⇒ оставить вне Health Score.")
    print()

    # ---------- A0.3 finalize 3-group structure ----------
    print("=" * 70)
    print("A0.3  Δ-correlation map для финальной группы (sanity)")
    print("=" * 70)
    # ориентированный delta map: все фичи в направлении «больше = лучше»
    feature_dirs = {
        "dead_search_rate": -1,
        "bounce_rate": -1,
        "km_median_time_to_cart_s": -1,
        "rate_clicks": +1,
        "rate_views": +1,
        "rate_favs": +1,
        "multi_category_rate": +1,
        "click_per_view": +1,
        "cart_per_click": +1,
        "cart_rate": +1,
        "session_conversion_rate": +1,
        "rate_unique_queries": +1,
        "rate_searches": +1,
        "view_per_search": +1,
        # search_refinement_rate — направление решает A0.1
    }
    # фиксируем здесь направление для отчёта
    sr_dir = +1 if out.loc[out["ref"] == "cart_rate", "corr_delta"].iloc[0] > 0 else -1
    feature_dirs["search_refinement_rate"] = sr_dir
    print(f"(зафиксировано direction search_refinement_rate = {sr_dir:+d})")
    print()
    df_d = daily[list(feature_dirs.keys())].diff().dropna()
    oriented = df_d.copy()
    for f, d in feature_dirs.items():
        oriented[f] = oriented[f] * d
    corr = oriented.corr().round(2)
    print(corr.to_string())
    print()
    n_pairs = len(feature_dirs) * (len(feature_dirs) - 1) // 2
    upper = corr.values[np.triu_indices_from(corr, k=1)]
    n_neg = int((upper < 0).sum())
    n_strong_neg = int((upper < -0.3).sum())
    print(
        f"Доля отрицательных пар после ориентации: {n_neg}/{n_pairs} ({100*n_neg/n_pairs:.0f}%)."
    )
    print(f"Сильно отрицательных (<-0.3): {n_strong_neg}")


if __name__ == "__main__":
    main()

"""
B3-B4: синтетические инъекции и сравнение delta-anomaly vs raw-MAD.

3 типа аномалий на dead_search_rate (фича в LOO_FEATURES и в quality):
  - Spike: один день +20%
  - Step:  +10% с дня X и далее
  - Drift: +1%·k каждый день в течение 10 дней

Для каждого канала:
  - Raw-MAD: today_value vs median ± 3·1.4826·MAD по baseline (как в new_metric §5.2)
  - Delta-MAD: Δscore_t vs train threshold (как в 05)

Метрики качества:
  precision = TP / (TP+FP), recall = TP / (TP+FN), time-to-detect (TTD)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import polars as pl
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
DAILY = ROOT / "data" / "daily_summaries"
OUT_DIR = ROOT / "src" / "metric" / "analysis" / "out"

ALL_FEATURES = [
    "dead_search_rate_inv",
    "bounce_rate_inv",
    "rate_clicks", "rate_views", "rate_favs", "multi_category_rate",
    "click_per_view", "cart_per_click",
    "mean_duration_s",
    "search_refinement_rate",
    "rate_unique_queries", "rate_searches", "view_per_search",
]
TARGETS = ["cart_rate", "session_conversion_rate", "km_speed_to_cart"]


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
    d["dead_search_rate_inv"] = 1 - d["dead_search_rate"]
    d["bounce_rate_inv"] = 1 - d["bounce_rate"]
    d["km_speed_to_cart"] = 1.0 / d["km_median_time_to_cart_s"]
    return d


def mad_threshold(series: np.ndarray, k: float = 3.0) -> tuple[float, float, float]:
    med = float(np.median(series))
    mad = float(np.median(np.abs(series - med)))
    return med - k * 1.4826 * mad, med, med + k * 1.4826 * mad


def inject_spike(values: np.ndarray, day_idx: int, magnitude: float) -> np.ndarray:
    out = values.copy()
    out[day_idx] = out[day_idx] * (1 + magnitude)
    return out


def inject_step(values: np.ndarray, day_idx: int, magnitude: float) -> np.ndarray:
    out = values.copy()
    out[day_idx:] = out[day_idx:] * (1 + magnitude)
    return out


def inject_drift(values: np.ndarray, day_idx: int, per_day: float, n_days: int) -> np.ndarray:
    out = values.copy()
    for k in range(n_days):
        if day_idx + k < len(out):
            out[day_idx + k] = out[day_idx + k] * (1 + per_day * (k + 1))
    return out


def evaluate_channel(
    alerts: Iterable[int],
    truth_days: set[int],
    n_total: int,
    inject_start: int,
) -> dict:
    alerts = set(alerts)
    tp = len(alerts & truth_days)
    fp = len(alerts - truth_days)
    fn = len(truth_days - alerts)
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    # TTD: первый алерт ≥ inject_start
    ttd = None
    for a in sorted(alerts):
        if a >= inject_start:
            ttd = a - inject_start
            break
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "ttd_days": ttd,
    }


def main() -> None:
    daily = load_daily().set_index("date")
    print(f"Daily: {len(daily)} дней")

    # baseline = первые 30, today/inject window = последние 31
    BASE = 30
    baseline = daily.iloc[:BASE]
    today = daily.iloc[BASE:]
    n_today = len(today)
    print(f"Baseline: {BASE}, Today (инжекция возможна): {n_today}")

    # raw-MAD пороги для dead_search_rate из baseline
    base_dsr = baseline["dead_search_rate"].values
    raw_lo, raw_med, raw_hi = mad_threshold(base_dsr)
    print(f"\nRaw-MAD пороги dead_search_rate: [{raw_lo:.4f}, {raw_hi:.4f}]  median={raw_med:.4f}")

    # delta-anomaly: фитим веса на baseline-delta-окне, считаем δ_score на today
    base_delta = daily.iloc[: BASE + 1].diff().dropna()
    today_delta_full = daily.diff().dropna()
    today_delta = today_delta_full.iloc[BASE - 1 :]  # delta-индекс смещён на 1
    print(f"Base-delta: {len(base_delta)}, Today-delta: {len(today_delta)}")

    xsc = StandardScaler().fit(base_delta[ALL_FEATURES])
    Xb = xsc.transform(base_delta[ALL_FEATURES])

    # Используем композит как «универсальный» сигнал (раздел B2)
    Yb = StandardScaler().fit_transform(base_delta[TARGETS])
    y_combo = Yb.mean(axis=1)
    ridge_c = Ridge(alpha=1.0).fit(Xb, y_combo)
    w = ridge_c.coef_
    # знак
    s_base = Xb @ w
    if np.mean([np.corrcoef(s_base, Yb[:, i])[0, 1] for i in range(Yb.shape[1])]) < 0:
        w = -w
        s_base = -s_base

    d_lo, d_med, d_hi = mad_threshold(s_base)
    print(f"Delta-MAD пороги: [{d_lo:.3f}, {d_hi:.3f}]  median={d_med:.3f}")

    # --- инжекции ---
    print("\n" + "=" * 70)
    print("СИНТЕТИЧЕСКИЕ ИНЪЕКЦИИ (dead_search_rate)")
    print("=" * 70)

    SCENARIOS = [
        # инъекции на quality-фичу (низкий delta-weight)
        ("dsr  spike +20%", "spike", "dead_search_rate", {"day_offset": 10, "magnitude": 0.20}, {10}),
        ("dsr  step  +10%", "step",  "dead_search_rate", {"day_offset": 10, "magnitude": 0.10}, set(range(10, n_today))),
        ("dsr  drift +1%/d×10d", "drift", "dead_search_rate", {"day_offset": 10, "per_day": 0.01, "n_days": 10}, set(range(10, 20))),
        # инъекции на high-weight engagement-фичу
        ("cpc  spike −20%", "spike", "cart_per_click", {"day_offset": 10, "magnitude": -0.20}, {10}),
        ("cpc  step  −10%", "step",  "cart_per_click", {"day_offset": 10, "magnitude": -0.10}, set(range(10, n_today))),
    ]

    results = []
    for name, kind, target_col, params, truth in SCENARIOS:
        col_today = today[target_col].values
        if kind == "spike":
            col_inj = inject_spike(col_today, params["day_offset"], params["magnitude"])
        elif kind == "step":
            col_inj = inject_step(col_today, params["day_offset"], params["magnitude"])
        else:
            col_inj = inject_drift(col_today, params["day_offset"], params["per_day"], params["n_days"])

        # raw-MAD пороги нужно пересчитать для каждой метрики
        base_col = baseline[target_col].values
        col_lo, _, col_hi = mad_threshold(base_col)
        # для anti-метрик алерт при value > hi; для positive — при value < lo
        # dead_search_rate — anti (rise = bad); cart_per_click — positive (drop = bad)
        if target_col in ("dead_search_rate",):
            raw_alerts = np.where(col_inj > col_hi)[0].tolist()
        else:
            raw_alerts = np.where(col_inj < col_lo)[0].tolist()

        # delta-MAD: пересобираем today-delta с инжектированной фичей
        inj_daily = today.copy()
        inj_daily[target_col] = col_inj
        if target_col == "dead_search_rate":
            inj_daily["dead_search_rate_inv"] = 1 - col_inj
        elif target_col == "bounce_rate":
            inj_daily["bounce_rate_inv"] = 1 - col_inj
        # склеиваем с baseline для расчёта дельт
        full = pd.concat([baseline, inj_daily])
        full_delta = full.diff().dropna()
        # today-delta-окно
        td_delta = full_delta.iloc[BASE - 1 :]
        X_td = xsc.transform(td_delta[ALL_FEATURES])
        s_td = X_td @ w
        # td_delta индексирует на (n_today + 1) — первый Δ — это переход с base→today_0,
        # позиция 1 == today_0, и т.д.
        # Алерт-индексация: алерт на день today_i ⇔ |s_td[i]| > threshold, где i == today-index
        # td_delta имеет len = n_today, индекс 0 = (today_0 − base_29), индекс i = (today_i − today_{i−1})
        d_alerts = []
        # s_td[i] = δ-score дельты ведущей К today_i (s_td[0] = base→today_0)
        for i in range(n_today):
            if s_td[i] < d_lo or s_td[i] > d_hi:
                d_alerts.append(i)

        truth_days = truth
        inj_start = params["day_offset"]
        raw_eval = evaluate_channel(raw_alerts, truth_days, n_today, inj_start)
        d_eval = evaluate_channel(d_alerts, truth_days, n_today, inj_start)

        print(f"\n[{name}]")
        print(f"  truth days = {sorted(truth_days)[:8]}{'…' if len(truth_days) > 8 else ''}")
        print(f"  raw-MAD:   alerts={sorted(raw_alerts)[:8]}  P={raw_eval['precision']:.2f}  R={raw_eval['recall']:.2f}  TTD={raw_eval['ttd_days']}")
        print(f"  delta-MAD: alerts={sorted(d_alerts)[:8]}    P={d_eval['precision']:.2f}  R={d_eval['recall']:.2f}  TTD={d_eval['ttd_days']}")

        results.append({"scenario": name, "raw": raw_eval, "delta": d_eval})

    # сводная
    print("\n" + "=" * 70)
    print("СВОДНАЯ")
    print("=" * 70)
    rows = []
    for r in results:
        rows.append(
            {
                "scenario": r["scenario"],
                "raw_P": r["raw"]["precision"],
                "raw_R": r["raw"]["recall"],
                "raw_TTD": r["raw"]["ttd_days"],
                "delta_P": r["delta"]["precision"],
                "delta_R": r["delta"]["recall"],
                "delta_TTD": r["delta"]["ttd_days"],
            }
        )
    summary = pd.DataFrame(rows).round(2)
    print(summary.to_string(index=False))

    # вердикт
    print("\n" + "=" * 70)
    print("ВЕРДИКТ ПО DELTA")
    print("=" * 70)
    advantages = []
    if any(r["delta"]["ttd_days"] is not None and r["raw"]["ttd_days"] is not None
           and r["delta"]["ttd_days"] < r["raw"]["ttd_days"] for r in results):
        advantages.append("delta detects faster on at least one scenario")
    if any(r["delta"]["recall"] > r["raw"]["recall"] + 0.10 for r in results):
        advantages.append("delta has materially higher recall on at least one scenario")
    if any(r["raw"]["recall"] > r["delta"]["recall"] + 0.10 for r in results):
        advantages.append("raw outperforms delta on at least one scenario (complementary)")

    if not advantages:
        print("delta не даёт явных преимуществ → не использовать или только как diagnostic")
    else:
        for a in advantages:
            print(f"  • {a}")

    # save
    out = {
        "raw_mad_thresholds": {"lo": raw_lo, "med": raw_med, "hi": raw_hi},
        "delta_mad_thresholds": {"lo": d_lo, "med": d_med, "hi": d_hi},
        "scenarios": results,
    }
    out_path = OUT_DIR / "synthetic_injection.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

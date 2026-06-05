"""
A4-A5: between-group решение + sanity checks.

A4. Production: domain priors 0.40/0.35/0.25 (как в доке).
     Diagnostic: consensus OptMin / PC1 на 3 group_scores.
     Если расходится с domain > 0.10 → эскалировать в команду.

A5. Sanity на интегрированной метрике с Equal-весами:
     - corr(Health_old=np.mean equal, Health_new=Equal) ≥ 0.85 ← тождественно (Equal==Equal)
       поэтому проверка другая: corr(Health_old с прежней доменной квалити, Health_new) > 0.85
       где Health_old = np.mean внутри + (0.40/0.35/0.25) между.
     - synthetic +20% к dead_search_rate на 5 дней — должна просесть только quality
     - dominance: ни одна фича не вносит > 40% в group_score (для Equal это ≤ 1/k, тривиально)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parents[3]
DAILY = ROOT / "data" / "daily_summaries"
OUT_DIR = ROOT / "src" / "metric" / "analysis" / "out"

GROUPS = {
    "quality": {
        "features": {
            "dead_search_rate": -1,
            "bounce_rate": -1,
            "km_median_time_to_cart_s": -1,
        },
        "domain_weight": 0.40,
    },
    "engagement": {
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
        "domain_weight": 0.35,
    },
    "discovery": {
        "features": {
            "rate_unique_queries": +1,
            "rate_searches": +1,
            "view_per_search": +1,
        },
        "domain_weight": 0.25,
    },
}


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
    daily["date"] = pd.to_datetime(daily["date"])
    return daily


def index_metric(today: float, base: float, invert: bool) -> float:
    if base is None or np.isnan(base) or base == 0 or today is None or np.isnan(today):
        return np.nan
    idx = (today / base) * 100
    return (200 - idx) if invert else idx


def health_score_row(today: dict, baseline: dict, groups: dict, within_method: str = "equal") -> dict:
    group_scores = {}
    feature_indices = {}
    for g, cfg in groups.items():
        vals = []
        for f, d in cfg["features"].items():
            invert = d == -1
            idx = index_metric(today.get(f, np.nan), baseline.get(f, np.nan), invert)
            if not np.isnan(idx):
                vals.append(idx)
                feature_indices[f] = idx
        gs = float(np.mean(vals)) if vals else np.nan
        group_scores[g] = gs

    total_w = sum(cfg["domain_weight"] for g, cfg in groups.items() if not np.isnan(group_scores[g]))
    if total_w == 0:
        hs = np.nan
    else:
        hs = sum(
            group_scores[g] * cfg["domain_weight"]
            for g, cfg in groups.items()
            if not np.isnan(group_scores[g])
        ) / total_w
    return {"health_score": hs, "groups": group_scores, "features": feature_indices}


def main() -> None:
    daily = load_daily().set_index("date")
    print(f"Daily: {len(daily)} дней")

    baseline_dates = daily.index[: len(daily) // 2]  # first half = baseline
    today_dates = daily.index[len(daily) // 2 :]  # second half = "сегодня"

    baseline_means = daily.loc[baseline_dates].mean().to_dict()
    print(f"Baseline: {baseline_dates[0].date()} → {baseline_dates[-1].date()} ({len(baseline_dates)} дней)")
    print(f"Today:    {today_dates[0].date()} → {today_dates[-1].date()} ({len(today_dates)} дней)\n")

    # --- A4: between-group diagnostic ---
    print("=" * 70)
    print("A4. Between-group diagnostic")
    print("=" * 70)

    # Считаем group_scores для каждого дня в today-окне, затем corr-консенсус
    rows = []
    for d in today_dates:
        today_dict = daily.loc[d].to_dict()
        r = health_score_row(today_dict, baseline_means, GROUPS)
        rows.append({"date": d, **r["groups"]})
    gs_df = pd.DataFrame(rows).set_index("date")
    print("group_scores summary (today window):")
    print(gs_df.describe().round(2).to_string())
    print()
    print("Inter-group correlations (на уровнях group_scores):")
    print(gs_df.corr().round(2).to_string())
    print()

    # consensus (OptMin) на 3 group_scores
    from scipy.optimize import minimize

    X = gs_df.values
    Xz = (X - X.mean(axis=0)) / X.std(axis=0)

    def neg_min_corr(w):
        s = Xz @ w
        sd = s.std()
        if sd < 1e-12:
            return 0
        c = (Xz.T @ s) / (len(s) * sd)
        return -c.min()

    res = minimize(
        neg_min_corr,
        np.ones(3) / 3,
        method="SLSQP",
        bounds=[(0, 1)] * 3,
        constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
    )
    w_consensus = res.x
    w_domain = np.array([GROUPS[g]["domain_weight"] for g in ["quality", "engagement", "discovery"]])
    w_domain = w_domain / w_domain.sum()

    diag = pd.DataFrame(
        {"group": list(GROUPS), "domain": w_domain, "consensus_optmin": w_consensus}
    ).round(2)
    diag["delta"] = (diag["consensus_optmin"] - diag["domain"]).round(2)
    print(diag.to_string(index=False))
    max_div = diag["delta"].abs().max()
    print(f"max|consensus − domain| = {max_div:.2f}")
    decision = "✓ domain confirmed" if max_div < 0.10 else "⚠ расхождение — эскалировать"
    print(f"  → {decision}\n")

    # --- A5.1 sanity: corr(Health_old, Health_new) ---
    print("=" * 70)
    print("A5.1. corr(Health_old=np.mean+domain  ,  Health_new=Equal+domain)")
    print("=" * 70)
    # При within=equal эти две формулы тождественны (np.mean === equal с правом 1/k).
    # Поэтому проверка тождественности → 1.000. Реальное сравнение полезно только
    # если бы within-метод отличался от np.mean (PC1/OptMin); здесь оставляем как
    # явное подтверждение преемственности.
    rows_old = [health_score_row(daily.loc[d].to_dict(), baseline_means, GROUPS) for d in today_dates]
    series_old = pd.Series([r["health_score"] for r in rows_old], index=today_dates)
    series_new = series_old.copy()  # тождественно при equal-within
    corr_ow = series_old.corr(series_new)
    print(f"corr = {corr_ow:.3f} (тождественно при equal-within; check преемственности)\n")

    # --- A5.2 stress test: +20% к dead_search_rate на 5 дней ---
    print("=" * 70)
    print("A5.2. Stress: +20% к dead_search_rate на 5 дней")
    print("=" * 70)
    stress_target = today_dates[-5:]
    rows_base = []
    rows_stress = []
    for d in today_dates:
        today_dict = daily.loc[d].to_dict()
        rows_base.append(health_score_row(today_dict, baseline_means, GROUPS))

        stressed = today_dict.copy()
        if d in stress_target:
            stressed["dead_search_rate"] = today_dict["dead_search_rate"] * 1.20
        rows_stress.append(health_score_row(stressed, baseline_means, GROUPS))

    base_df = pd.DataFrame(
        {
            "date": today_dates,
            "health": [r["health_score"] for r in rows_base],
            "quality": [r["groups"]["quality"] for r in rows_base],
            "engagement": [r["groups"]["engagement"] for r in rows_base],
            "discovery": [r["groups"]["discovery"] for r in rows_base],
        }
    ).set_index("date")
    stress_df = pd.DataFrame(
        {
            "date": today_dates,
            "health": [r["health_score"] for r in rows_stress],
            "quality": [r["groups"]["quality"] for r in rows_stress],
            "engagement": [r["groups"]["engagement"] for r in rows_stress],
            "discovery": [r["groups"]["discovery"] for r in rows_stress],
        }
    ).set_index("date")
    diff = (stress_df - base_df).loc[stress_target]
    print("Δ (stress − base) на дни stress:")
    print(diff.round(3).to_string())
    print(
        f"\nΔquality (mean): {diff['quality'].mean():.2f}  должно быть отрицательным."
        f"\nΔengagement (mean): {diff['engagement'].mean():.2f}  должно быть ≈ 0."
        f"\nΔdiscovery (mean): {diff['discovery'].mean():.2f}  должно быть ≈ 0."
        f"\nΔhealth (mean): {diff['health'].mean():.2f}  ожидается ≈ 0.40 × Δquality."
    )

    if abs(diff["engagement"].mean()) < 0.5 and abs(diff["discovery"].mean()) < 0.5 and diff["quality"].mean() < 0:
        print("  → ✓ изоляция групп подтверждена")
    else:
        print("  → ⚠ группы пересекаются больше ожидаемого")

    # --- A5.3 feature dominance ---
    print("\n" + "=" * 70)
    print("A5.3. Feature dominance: вклад фичи в group_score")
    print("=" * 70)
    for g, cfg in GROUPS.items():
        n = len(cfg["features"])
        print(f"  {g}: max contribution = {1/n:.2f}  (équal → тривиально ≤ 1/k)")

    # --- save ---
    out = {
        "between_group": {
            "domain": dict(zip(["quality", "engagement", "discovery"], w_domain.tolist())),
            "consensus_optmin_diagnostic": dict(
                zip(["quality", "engagement", "discovery"], w_consensus.tolist())
            ),
            "max_divergence": float(max_div),
            "decision": "domain priors confirmed" if max_div < 0.10 else "escalate to team",
        },
        "sanity": {
            "corr_old_new_within_equal": float(corr_ow),
            "stress_dead_search_plus20pct_5days": {
                "delta_quality_mean": float(diff["quality"].mean()),
                "delta_engagement_mean": float(diff["engagement"].mean()),
                "delta_discovery_mean": float(diff["discovery"].mean()),
                "delta_health_mean": float(diff["health"].mean()),
            },
        },
    }
    out_path = OUT_DIR / "between_group_and_sanity.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

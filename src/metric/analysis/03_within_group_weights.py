"""
A1-A3: per-group consensus weights с bootstrap-стабильностью.

Группы (после A0):
  quality     = [dead_search ↓, bounce ↓, km_median ↓]      (refinement переехал в engagement)
  engagement  = [rate_clicks, rate_views, rate_favs, multi_category, click_per_view,
                 cart_per_click, cart_rate, session_conversion_rate, mean_duration_s,
                 search_refinement_rate]
  discovery   = [rate_unique_queries, rate_searches, view_per_search]

Для каждой группы:
  1) Equal / PC1 / OptMin на z-нормализованных ориентированных фичах
  2) Bootstrap по дням (1000) → 95% CI весов
  3) Time-split CV (first half / second half) → max relative diff
  4) Sensitivity к удалению фичи

Decision rule (per group):
  if PC1.expvar > 0.50 AND all loadings > 0 AND PC1 bootstrap-stable: → PC1
  elif bootstrap CI всех методов перекрываются: → Equal
  elif OptMin.min_corr > Equal.min_corr + 0.10 AND stable: → OptMin
  else: → Equal
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from scipy.optimize import minimize
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
DAILY = ROOT / "data" / "daily_summaries"
OUT_DIR = ROOT / "src" / "metric" / "analysis" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RNG = np.random.default_rng(42)

# Финальный конфиг групп (после A0)
GROUPS: dict[str, dict] = {
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


def orient_z(daily: pd.DataFrame, features: dict[str, int]) -> tuple[np.ndarray, list[str]]:
    feats = list(features.keys())
    dirs = np.array([features[f] for f in feats])
    raw = daily[feats].values * dirs
    # ffill any internal NaN before scaling — for stability of small samples
    raw = pd.DataFrame(raw, columns=feats).ffill().bfill().values
    Z = StandardScaler().fit_transform(raw)
    return Z, feats


def equal_weights(k: int) -> np.ndarray:
    return np.ones(k) / k


def pc1_weights(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Returns (loadings_signed, weights_positive_normalized, explained_var)."""
    C = np.corrcoef(X.T)
    vals, vecs = np.linalg.eigh(C)
    pc1 = vecs[:, -1]
    expvar = vals[-1] / vals.sum()
    if pc1.sum() < 0:
        pc1 = -pc1
    w = np.abs(pc1) / np.abs(pc1).sum()
    return pc1, w, expvar


def optmin_weights(X: np.ndarray) -> np.ndarray:
    k = X.shape[1]

    def neg_min_corr(w: np.ndarray, X: np.ndarray) -> float:
        s = X @ w
        sd = s.std()
        if sd < 1e-12:
            return 0.0
        corrs = (X.T @ s) / (X.shape[0] * sd)
        return -float(corrs.min())

    w0 = np.ones(k) / k
    res = minimize(
        neg_min_corr,
        w0,
        args=(X,),
        method="SLSQP",
        bounds=[(0, 1)] * k,
        constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
        options={"maxiter": 500, "ftol": 1e-9},
    )
    return res.x


def score_corrs(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    s = X @ w
    sd = s.std()
    if sd < 1e-12:
        return np.zeros(X.shape[1])
    return (X.T @ s) / (X.shape[0] * sd)


def bootstrap_weights(X: np.ndarray, method: str, n_boot: int = 1000) -> np.ndarray:
    """Returns shape (n_boot, k)."""
    n, k = X.shape
    out = np.zeros((n_boot, k))
    for b in range(n_boot):
        idx = RNG.integers(0, n, n)
        Xb = X[idx]
        if method == "equal":
            out[b] = equal_weights(k)
        elif method == "pc1":
            try:
                _, w, _ = pc1_weights(Xb)
            except Exception:
                w = equal_weights(k)
            out[b] = w
        elif method == "optmin":
            try:
                out[b] = optmin_weights(Xb)
            except Exception:
                out[b] = equal_weights(k)
    return out


def evaluate_group(name: str, daily: pd.DataFrame, features: dict[str, int]) -> dict:
    print(f"\n{'='*70}\nGROUP: {name}  ({len(features)} features)\n{'='*70}")
    X, feats = orient_z(daily, features)
    n = X.shape[0]

    # Базовые веса
    w_eq = equal_weights(len(feats))
    pc1_signed, w_pc1, pc1_expvar = pc1_weights(X)
    w_opt = optmin_weights(X)

    # corrs(score, feature)
    c_eq = score_corrs(w_eq, X)
    c_pc1 = score_corrs(w_pc1 * np.sign(pc1_signed), X)
    c_opt = score_corrs(w_opt, X)

    weights_df = pd.DataFrame({"feature": feats, "Equal": w_eq, "PC1": w_pc1, "OptMin": w_opt})
    corrs_df = pd.DataFrame({"feature": feats, "Equal": c_eq, "PC1": c_pc1, "OptMin": c_opt})

    print("\nweights:")
    print(weights_df.round(3).to_string(index=False))
    print(f"\nPC1 explained variance: {pc1_expvar*100:.1f}%")
    print(f"PC1 loadings all positive: {(pc1_signed > 0).all()}  ({(pc1_signed < 0).sum()} negative)")
    print("\ncorr(score, feature):")
    print(corrs_df.round(3).to_string(index=False))
    print(
        f"\nsummary:\n  Equal  mean={c_eq.mean():.3f}  min={c_eq.min():.3f}\n"
        f"  PC1    mean={c_pc1.mean():.3f}  min={c_pc1.min():.3f}\n"
        f"  OptMin mean={c_opt.mean():.3f}  min={c_opt.min():.3f}"
    )

    # Bootstrap
    print("\nBootstrap (1000 ресэмплов по дням):")
    boots = {m: bootstrap_weights(X, m) for m in ("equal", "pc1", "optmin")}
    boot_summary = {}
    for m, B in boots.items():
        ci_lo = np.quantile(B, 0.025, axis=0)
        ci_hi = np.quantile(B, 0.975, axis=0)
        med = np.median(B, axis=0)
        ci_width = ci_hi - ci_lo
        rel_width = ci_width / np.maximum(np.abs(med), 1e-6)
        boot_summary[m] = {
            "median": med,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
            "ci_width": ci_width,
            "max_rel_width": float(rel_width.max()),
        }
    bdf = pd.DataFrame(
        {
            "feature": feats,
            "PC1_median": boot_summary["pc1"]["median"],
            "PC1_ci_lo": boot_summary["pc1"]["ci_lo"],
            "PC1_ci_hi": boot_summary["pc1"]["ci_hi"],
            "OptMin_median": boot_summary["optmin"]["median"],
            "OptMin_ci_lo": boot_summary["optmin"]["ci_lo"],
            "OptMin_ci_hi": boot_summary["optmin"]["ci_hi"],
        }
    ).round(3)
    print(bdf.to_string(index=False))
    print(f"  PC1    max_rel_CI_width = {boot_summary['pc1']['max_rel_width']:.2f}")
    print(f"  OptMin max_rel_CI_width = {boot_summary['optmin']['max_rel_width']:.2f}")

    # Time-split CV
    print("\nTime-split CV (first half vs second half):")
    h = n // 2
    cv_rows = []
    for m, fn in [("equal", lambda Xs: equal_weights(Xs.shape[1])),
                  ("pc1", lambda Xs: pc1_weights(Xs)[1]),
                  ("optmin", optmin_weights)]:
        w_full = fn(X)
        w_h1 = fn(X[:h])
        w_h2 = fn(X[h:])
        max_diff = float(np.max(np.abs(w_h1 - w_h2)))
        rel_diff = max_diff / max(w_full.max(), 1e-6)
        cv_rows.append({"method": m, "max_abs_diff": max_diff, "max_rel_diff_vs_full": rel_diff})
    cv_df = pd.DataFrame(cv_rows).round(3)
    print(cv_df.to_string(index=False))

    # Sensitivity to drop
    print("\nSensitivity to feature drop (PC1):")
    sens_rows = []
    _, w_full_pc1, _ = pc1_weights(X)
    for i, f in enumerate(feats):
        mask = np.ones(len(feats), bool)
        mask[i] = False
        X_drop = X[:, mask]
        _, w_drop, _ = pc1_weights(X_drop)
        # переразложим в полный размер
        w_full_padded = np.zeros(len(feats))
        w_full_padded[mask] = w_drop * (1 - w_full_pc1[i])  # нормировать
        diff = np.abs(w_full_padded - w_full_pc1)
        sens_rows.append({"dropped": f, "max_other_change": float(diff[mask].max())})
    sens_df = pd.DataFrame(sens_rows).round(3)
    print(sens_df.sort_values("max_other_change", ascending=False).to_string(index=False))

    # Decision rule
    print("\nDecision:")
    pc1_stable = boot_summary["pc1"]["max_rel_width"] < 2.0
    opt_stable = boot_summary["optmin"]["max_rel_width"] < 2.0
    overlaps = True
    for i in range(len(feats)):
        eq_v = 1.0 / len(feats)
        if not (boot_summary["pc1"]["ci_lo"][i] <= eq_v <= boot_summary["pc1"]["ci_hi"][i]):
            overlaps = False
            break

    chosen = None
    reason = ""
    if pc1_expvar > 0.50 and (pc1_signed > 0).all() and pc1_stable:
        chosen, reason = "PC1", f"expvar={pc1_expvar:.2f}>0.5, все loadings>0, bootstrap стабилен"
    elif overlaps:
        chosen, reason = "Equal", "bootstrap CI PC1 включают равные веса — consensus не делает работы"
    elif c_opt.min() > c_eq.min() + 0.10 and opt_stable:
        chosen, reason = "OptMin", (
            f"min_corr OptMin={c_opt.min():.2f} > Equal={c_eq.min():.2f}+0.10, стабилен"
        )
    else:
        chosen, reason = "Equal", "ни PC1 ни OptMin не дают надёжного преимущества"
    print(f"  → {chosen} ({reason})")

    chosen_weights = {"Equal": w_eq, "PC1": w_pc1, "OptMin": w_opt}[chosen]
    return {
        "group": name,
        "features": feats,
        "directions": [features[f] for f in feats],
        "method": chosen,
        "reason": reason,
        "weights": {f: float(w) for f, w in zip(feats, chosen_weights)},
        "diagnostics": {
            "pc1_expvar": float(pc1_expvar),
            "pc1_all_positive": bool((pc1_signed > 0).all()),
            "min_corr": {"Equal": float(c_eq.min()), "PC1": float(c_pc1.min()), "OptMin": float(c_opt.min())},
            "mean_corr": {"Equal": float(c_eq.mean()), "PC1": float(c_pc1.mean()), "OptMin": float(c_opt.mean())},
            "bootstrap_max_rel_width": {
                "PC1": boot_summary["pc1"]["max_rel_width"],
                "OptMin": boot_summary["optmin"]["max_rel_width"],
            },
            "cv_max_abs_diff": {r["method"]: r["max_abs_diff"] for r in cv_rows},
        },
    }


def main() -> None:
    daily = load_daily()
    print(f"Daily: {len(daily)} дней")

    results = {g: evaluate_group(g, daily, cfg["features"]) for g, cfg in GROUPS.items()}

    out_path = OUT_DIR / "within_group_results.json"
    with open(out_path, "w") as fp:
        json.dump(results, fp, indent=2, ensure_ascii=False)
    print(f"\nResults → {out_path}")

    print("\n" + "=" * 70)
    print("ИТОГО (для metric_choice.md)")
    print("=" * 70)
    for g, r in results.items():
        print(f"\n[{g}]  method = {r['method']}")
        print(f"  reason: {r['reason']}")
        for f, w in r["weights"].items():
            print(f"    {f:30s}  {w:.3f}")


if __name__ == "__main__":
    main()

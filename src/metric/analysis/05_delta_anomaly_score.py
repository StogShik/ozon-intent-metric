"""
B1-B2: правильный hold-out + δ-score как anomaly detector.

LOO-set (исключаем источники таргетов):
  ALL_FEATURES = 14 фичей Health Score (без NLP, без km_speed pseudo-feature)
  LOO_FEATURES = ALL_FEATURES минус {cart_rate, session_conversion_rate, km_median_time_to_cart_s}
                   = 11 фичей
  TARGETS = [cart_rate, session_conversion_rate, km_speed = 1/km_median]

B1 fix: фитим веса ТОЛЬКО на train-окне (первые n-N_val дельт);
        валидируем на последних N_val.

B2: δ_score_t = ΔX_loo_t @ w_loo (signed). Anomaly threshold = median ± 3·1.4826·MAD
    на train-окне.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from sklearn.cross_decomposition import CCA, PLSRegression
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
DAILY = ROOT / "data" / "daily_summaries"
OUT_DIR = ROOT / "src" / "metric" / "analysis" / "out"

ALL_FEATURES = [
    # quality (без km_median — он источник km_speed таргета)
    "dead_search_rate_inv",
    "bounce_rate_inv",
    "km_median_time_to_cart_s",
    # engagement
    "rate_clicks", "rate_views", "rate_favs", "multi_category_rate",
    "click_per_view", "cart_per_click",
    "cart_rate", "session_conversion_rate",
    "mean_duration_s",
    "search_refinement_rate",
    # discovery
    "rate_unique_queries", "rate_searches", "view_per_search",
]

TARGET_SOURCE = {
    "cart_rate": "cart_rate",
    "session_conversion_rate": "session_conversion_rate",
    "km_speed_to_cart": "km_median_time_to_cart_s",
}
TARGETS = list(TARGET_SOURCE)

LOO_FEATURES = [f for f in ALL_FEATURES if f not in TARGET_SOURCE.values()]
N_VAL = 14  # последние 14 дельт идут в hold-out


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


def main() -> None:
    daily = load_daily().set_index("date")
    print(f"Daily: {len(daily)} дней")

    delta = daily.diff().dropna()
    print(f"Delta: {len(delta)} строк")

    train = delta.iloc[:-N_VAL]
    val = delta.iloc[-N_VAL:]
    print(f"Train: {len(train)}  Val: {len(val)} (последние {N_VAL} дельт)")

    # Z-нормировка на train, применить к val
    xsc = StandardScaler().fit(train[LOO_FEATURES])
    ysc = StandardScaler().fit(train[TARGETS])
    Xtr = xsc.transform(train[LOO_FEATURES])
    Ytr = ysc.transform(train[TARGETS])
    Xv = xsc.transform(val[LOO_FEATURES])
    Yv = ysc.transform(val[TARGETS])

    # --- модели (фитятся ТОЛЬКО на train) ---
    methods: dict[str, np.ndarray] = {}

    # Ridge per-target
    for i, t in enumerate(TARGETS):
        ridge = Ridge(alpha=1.0).fit(Xtr, Ytr[:, i])
        methods[f"Ridge[{t}]"] = ridge.coef_

    # PLS multi-target
    pls = PLSRegression(n_components=1, scale=False).fit(Xtr, Ytr)
    methods["PLS"] = pls.x_weights_[:, 0]

    # CCA multi-target
    cca = CCA(n_components=1, scale=False).fit(Xtr, Ytr)
    methods["CCA"] = cca.x_weights_[:, 0]

    # Composite
    y_combo = Ytr.mean(axis=1)
    ridge_c = Ridge(alpha=1.0).fit(Xtr, y_combo)
    methods["Composite"] = ridge_c.coef_

    # выравниваем знак по среднему corr с таргетами на train
    for m, w in methods.items():
        s = Xtr @ w
        avg = np.mean([np.corrcoef(s, Ytr[:, i])[0, 1] for i in range(Ytr.shape[1])])
        if avg < 0:
            methods[m] = -w

    print("\nweights matrix (signed, fitted on train):")
    W = pd.DataFrame(methods, index=LOO_FEATURES).round(3)
    print(W.to_string())

    # --- B1: hold-out корреляции на val ---
    print("\n" + "=" * 70)
    print("B1. Hold-out validation (val ≠ train, fitting on train only)")
    print("=" * 70)
    rows = []
    for m, w in methods.items():
        s_val = Xv @ w
        corrs = [np.corrcoef(s_val, Yv[:, i])[0, 1] for i in range(Yv.shape[1])]
        rows.append(
            {
                "method": m,
                "corr_cart_rate": corrs[0],
                "corr_session_conv": corrs[1],
                "corr_km_speed": corrs[2],
                "mean_abs_corr": float(np.mean(np.abs(corrs))),
            }
        )
    holdout = pd.DataFrame(rows).round(3)
    print(holdout.to_string(index=False))
    best = holdout.sort_values("mean_abs_corr", ascending=False).iloc[0]
    print(f"\n→ лучший по mean|corr|: {best['method']} ({best['mean_abs_corr']:.3f})")

    # --- B2: anomaly threshold на train ---
    print("\n" + "=" * 70)
    print("B2. Anomaly threshold (на train δ_score)")
    print("=" * 70)
    chosen = "Composite"  # как «универсальный» — наиболее симметричный
    w = methods[chosen]
    s_train = Xtr @ w
    med = float(np.median(s_train))
    mad = float(np.median(np.abs(s_train - med)))
    sigma = 1.4826 * mad
    thresholds = (med - 3 * sigma, med + 3 * sigma)
    print(f"chosen method: {chosen}")
    print(f"  median(δ_train) = {med:.3f}")
    print(f"  MAD             = {mad:.3f}")
    print(f"  thresholds (±3·1.4826·MAD) = ({thresholds[0]:.2f}, {thresholds[1]:.2f})")
    s_val = Xv @ w
    val_alerts = ((s_val < thresholds[0]) | (s_val > thresholds[1])).sum()
    print(f"  alerts на val (без инжекции): {val_alerts}/{len(s_val)} дней")

    # сохраняем
    out = {
        "loo_features": LOO_FEATURES,
        "targets": TARGETS,
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "weights": {m: w.tolist() for m, w in methods.items()},
        "holdout_corrs": holdout.to_dict(orient="records"),
        "anomaly_threshold_method": chosen,
        "threshold": {
            "median_train": med,
            "mad_train": mad,
            "lower": thresholds[0],
            "upper": thresholds[1],
        },
    }
    out_path = OUT_DIR / "delta_anomaly.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

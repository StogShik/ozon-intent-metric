"""
C1. False-positive rate raw-MAD per feature на чистом baseline (LOO-style).

Идея: для каждой фичи и каждого дня d считаем порог по leave-one-out window
(все дни кроме d), смотрим, попадает ли d в коридор. FP rate = доля дней,
которые «алертит» сама на себе. На чистых данных это и есть базовая шумность
канала.

Pass: FP rate ≤ 5% на всех 16 фичах при k=3.
Если FP > 5% — рекомендуем поднять k до 3.5 или winsorize p99.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ALL_FEATURES, ANTI, OUT_DIR, load_daily, mad_threshold


def loo_alerts(values: np.ndarray, k: float = 3.0) -> np.ndarray:
    """Для каждого индекса i считает порог по остальным; True если i — алерт."""
    n = len(values)
    flags = np.zeros(n, dtype=bool)
    for i in range(n):
        rest = np.delete(values, i)
        lo, _, hi = mad_threshold(rest, k=k)
        v = values[i]
        if np.isnan(v):
            continue
        flags[i] = v < lo or v > hi
    return flags


def main() -> None:
    daily = load_daily().set_index("date")
    n = len(daily)
    print(f"Baseline window: {n} дней ({daily.index[0].date()} → {daily.index[-1].date()})\n")

    rows = []
    for k in (3.0, 3.5):
        for f in ALL_FEATURES:
            vals = daily[f].values.astype(float)
            flags = loo_alerts(vals, k=k)
            n_valid = int(np.sum(~np.isnan(vals)))
            fp_rate = float(flags.sum()) / max(n_valid, 1)
            rows.append(
                {
                    "feature": f,
                    "k": k,
                    "n_alerts": int(flags.sum()),
                    "n_valid_days": n_valid,
                    "fp_rate": fp_rate,
                    "is_anti": f in ANTI,
                }
            )

    df = pd.DataFrame(rows)
    print("=" * 70)
    print("FP rate per feature (LOO raw-MAD)")
    print("=" * 70)
    piv = df.pivot(index="feature", columns="k", values="fp_rate").round(3)
    piv.columns = [f"k={c}" for c in piv.columns]
    print(piv.to_string())

    print()
    print("=" * 70)
    print("Pass/Fail при k=3 (порог 5%)")
    print("=" * 70)
    k3 = df[df["k"] == 3.0].sort_values("fp_rate", ascending=False)
    for _, r in k3.iterrows():
        status = "✓" if r["fp_rate"] <= 0.05 else "✗"
        print(f"  {status} {r['feature']:<32}  fp={r['fp_rate']:.3f}  ({r['n_alerts']}/{r['n_valid_days']})")

    n_fail_3 = int((k3["fp_rate"] > 0.05).sum())
    n_fail_35 = int((df[df["k"] == 3.5]["fp_rate"] > 0.05).sum())
    print(f"\nfail count: k=3 → {n_fail_3}/{len(k3)},  k=3.5 → {n_fail_35}/{len(k3)}")

    verdict = "PASS" if n_fail_3 == 0 else ("PASS_AT_3.5" if n_fail_35 == 0 else "FAIL")
    print(f"VERDICT: {verdict}")
    if verdict == "PASS_AT_3.5":
        print("  → рекомендуем k=3.5 в production")
    elif verdict == "FAIL":
        worst = k3.iloc[0]
        print(f"  → worst: {worst['feature']} fp={worst['fp_rate']:.3f} (k=3.5)")
        print("  → нужен winsorize p99 или рассмотреть log-transform для фичи")

    out = {
        "n_days": n,
        "per_feature": df.to_dict(orient="records"),
        "verdict": verdict,
        "fail_count_k3": n_fail_3,
        "fail_count_k3_5": n_fail_35,
    }
    out_path = OUT_DIR / "fp_rate_baseline.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

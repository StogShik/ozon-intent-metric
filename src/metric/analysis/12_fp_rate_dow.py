"""
C1-fix: повторный прогон 07_fp_rate_baseline.py, но с DOW-стратифицированным
raw-MAD из src/metric/anomaly.py.

LOO-схема: для каждого дня d убираем его из baseline, фитим detector, проверяем,
алертит ли он сам себя. На чистых данных это базовая шумность канала.

Цель: FP rate ≤ 5% при k=3 на всех 16 фичах.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import ANTI, ALL_FEATURES, GROUPS, OUT_DIR, load_daily

from anomaly import MadDetector


FEATURE_DIRECTIONS = {f: d for g in GROUPS.values() for f, d in g["features"].items()}


def loo_fp_rate(daily: pd.DataFrame, features: dict[str, int], k: float) -> pd.DataFrame:
    n = len(daily)
    rows = []
    for i in range(n):
        rest = daily.drop(daily.index[i])
        det = MadDetector.fit(rest, features, k=k)
        dow = int(daily.index[i].dayofweek)
        for f in features:
            val = float(daily[f].iloc[i])
            alert_2s = det.check_two_sided(f, val, dow)
            alert_dir = det.check(f, val, dow)
            r = det.residual(f, val, dow)
            rows.append(
                {
                    "feature": f,
                    "day_idx": i,
                    "dow": dow,
                    "value": val,
                    "residual": r,
                    "alert_two_sided": alert_2s,
                    "alert_directional": alert_dir,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    daily = load_daily().set_index("date")
    n = len(daily)
    print(f"Baseline: {n} дней ({daily.index[0].date()} → {daily.index[-1].date()})")
    print(f"Days-per-DOW counts: {dict(pd.Series(daily.index.dayofweek).value_counts().sort_index())}\n")

    rows_per_k = []
    for k in (3.0, 3.5):
        df = loo_fp_rate(daily, FEATURE_DIRECTIONS, k=k)
        per_f = df.groupby("feature").agg(
            n_alerts_2s=("alert_two_sided", "sum"),
            n_alerts_dir=("alert_directional", "sum"),
            n_valid=("value", lambda s: int((~np.isnan(s)).sum())),
        )
        per_f["fp_rate"] = per_f["n_alerts_dir"] / per_f["n_valid"]  # production direction-aware
        per_f["fp_rate_two_sided"] = per_f["n_alerts_2s"] / per_f["n_valid"]
        per_f["k"] = k
        rows_per_k.append(per_f.reset_index())

    full = pd.concat(rows_per_k, ignore_index=True)

    print("=" * 78)
    print("DOW-stratified raw-MAD: FP rate per feature")
    print("=" * 78)
    piv = full.pivot(index="feature", columns="k", values="fp_rate").round(3)
    piv.columns = [f"k={c}" for c in piv.columns]
    print(piv.to_string())

    print()
    print("=" * 78)
    print("Pass/Fail при k=3 (порог 5%)")
    print("=" * 78)
    k3 = full[full["k"] == 3.0].sort_values("fp_rate", ascending=False)
    for _, r in k3.iterrows():
        status = "✓" if r["fp_rate"] <= 0.05 else "✗"
        print(
            f"  {status} {r['feature']:<32}  fp_dir={r['fp_rate']:.3f}  "
            f"({int(r['n_alerts_dir'])}/{r['n_valid']})  fp_2s={r['fp_rate_two_sided']:.3f}"
        )

    n_fail_3 = int((k3["fp_rate"] > 0.05).sum())
    n_fail_35 = int((full[full["k"] == 3.5]["fp_rate"] > 0.05).sum())
    print(f"\nfail count: k=3 → {n_fail_3}/{len(k3)},  k=3.5 → {n_fail_35}/{len(k3)}")

    verdict = "PASS" if n_fail_3 == 0 else ("PASS_AT_3.5" if n_fail_35 == 0 else "FAIL")
    print(f"VERDICT: {verdict}")

    # сравнение со старым 07 (global MAD)
    prev = OUT_DIR / "fp_rate_baseline.json"
    if prev.exists():
        with open(prev) as fp:
            old = json.load(fp)
        print("\nСравнение со старым 07 (global MAD):")
        old_k3 = {r["feature"]: r["fp_rate"] for r in old["per_feature"] if r["k"] == 3.0}
        new_k3 = dict(zip(k3["feature"], k3["fp_rate"]))
        cmp_rows = []
        for f in ALL_FEATURES:
            cmp_rows.append({"feature": f, "global": old_k3[f], "dow": new_k3[f], "delta": new_k3[f] - old_k3[f]})
        cmp_df = pd.DataFrame(cmp_rows).round(3).sort_values("delta")
        print(cmp_df.to_string(index=False))
        improved = int((cmp_df["delta"] < 0).sum())
        unchanged = int((cmp_df["delta"] == 0).sum())
        worsened = int((cmp_df["delta"] > 0).sum())
        print(f"\nimproved: {improved},  unchanged: {unchanged},  worsened: {worsened}")

    out = {
        "n_days": n,
        "per_feature_k3": k3.drop(columns=["k"]).to_dict(orient="records"),
        "verdict": verdict,
        "fail_count_k3": n_fail_3,
        "fail_count_k3_5": n_fail_35,
        "comparison_vs_global": cmp_df.to_dict(orient="records") if prev.exists() else None,
    }
    out_path = OUT_DIR / "fp_rate_dow.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False, default=str)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

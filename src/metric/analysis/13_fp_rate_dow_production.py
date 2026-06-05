"""
C1-fix-prod: FP rate в production-сетапе (fixed baseline = первые 30 дней,
today = последние 31). Это ближе к реальному использованию, чем LOO,
и шире коридор → ниже FP rate.

Detector: adaptive DOW + direction-aware (из anomaly.py).
Цель: FP rate ≤ 5% на всех 16 фичах при k=3.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import GROUPS, OUT_DIR, load_daily

from anomaly import MadDetector


FEATURE_DIRECTIONS = {f: d for g in GROUPS.values() for f, d in g["features"].items()}


def main() -> None:
    daily = load_daily().set_index("date")
    n = len(daily)
    BASE = 30
    baseline = daily.iloc[:BASE]
    today = daily.iloc[BASE:]
    print(f"Daily: {n}. Baseline: {BASE} ({baseline.index[0].date()} → {baseline.index[-1].date()}).")
    print(f"Today: {len(today)} ({today.index[0].date()} → {today.index[-1].date()}).\n")

    rows_per_k = []
    for k in (3.0, 3.5):
        det = MadDetector.fit(baseline, FEATURE_DIRECTIONS, k=k)
        rows = []
        for d, day in zip(today.index, today.itertuples()):
            dow = int(d.dayofweek)
            for f in FEATURE_DIRECTIONS:
                val = float(today.loc[d, f])
                rows.append(
                    {
                        "feature": f,
                        "k": k,
                        "alert_dir": det.check(f, val, dow),
                        "alert_2s": det.check_two_sided(f, val, dow),
                        "used_dow": det.use_dow[f],
                    }
                )
        df = pd.DataFrame(rows)
        per_f = df.groupby("feature").agg(
            n_alerts_dir=("alert_dir", "sum"),
            n_alerts_2s=("alert_2s", "sum"),
            used_dow=("used_dow", "first"),
        )
        per_f["n_today"] = len(today)
        per_f["fp_rate"] = per_f["n_alerts_dir"] / per_f["n_today"]
        per_f["fp_rate_2s"] = per_f["n_alerts_2s"] / per_f["n_today"]
        per_f["k"] = k
        rows_per_k.append(per_f.reset_index())

    full = pd.concat(rows_per_k, ignore_index=True)

    print("=" * 78)
    print("Production setup: fixed baseline=30, today=31, direction-aware FP rate")
    print("=" * 78)
    for k in (3.0, 3.5):
        print(f"\n--- k={k} ---")
        sub = full[full["k"] == k].sort_values("fp_rate", ascending=False)
        for _, r in sub.iterrows():
            ok = "✓" if r["fp_rate"] <= 0.05 else "✗"
            dow_tag = "DOW" if r["used_dow"] else "global"
            print(
                f"  {ok} {r['feature']:<32}  fp={r['fp_rate']:.3f}  "
                f"({int(r['n_alerts_dir'])}/{int(r['n_today'])})  [{dow_tag}]"
            )

    n_fail_3 = int((full[full["k"] == 3.0]["fp_rate"] > 0.05).sum())
    n_fail_35 = int((full[full["k"] == 3.5]["fp_rate"] > 0.05).sum())
    print(f"\nfail count (production): k=3 → {n_fail_3}/16,  k=3.5 → {n_fail_35}/16")

    verdict = "PASS" if n_fail_3 == 0 else ("PASS_AT_3.5" if n_fail_35 == 0 else "FAIL")
    print(f"VERDICT: {verdict}")

    out = {
        "n_baseline": BASE,
        "n_today": int(len(today)),
        "results": full.to_dict(orient="records"),
        "verdict": verdict,
        "fail_count_k3": n_fail_3,
        "fail_count_k3_5": n_fail_35,
    }
    out_path = OUT_DIR / "fp_rate_dow_production.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False, default=str)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

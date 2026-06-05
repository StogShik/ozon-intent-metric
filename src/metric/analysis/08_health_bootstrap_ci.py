"""
C2. Bootstrap CI на Health Score (и group_scores).

Метод:
- Baseline = первые 30 дней, today_window = последние 31.
- 1000 итераций: resample today_dates с возвратом → пересчитать health/group_scores
  → собрать 95% CI на median.
- Считаем «шум» как ширину 95% CI median(health) на чистых данных.

Pass: ширина 95% CI median(health) ≤ 5 пунктов.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT_DIR, health_score_row, load_daily


N_ITER = 1000
SEED = 42


def main() -> None:
    daily = load_daily().set_index("date")
    print(f"Daily: {len(daily)} дней")

    BASE = 30
    baseline = daily.iloc[:BASE]
    today = daily.iloc[BASE:]
    baseline_means = baseline.mean(numeric_only=True).to_dict()
    print(f"Baseline: {baseline.index[0].date()} → {baseline.index[-1].date()} ({BASE})")
    print(f"Today:    {today.index[0].date()} → {today.index[-1].date()} ({len(today)})\n")

    # точечные значения
    rows = [health_score_row(today.loc[d].to_dict(), baseline_means) for d in today.index]
    point = pd.DataFrame(rows, index=today.index)
    print("Point distribution today-window:")
    print(point.describe().round(2).to_string())
    print()

    # bootstrap median
    rng = np.random.default_rng(SEED)
    n = len(today)
    samples = {"health": [], "quality": [], "engagement": [], "discovery": []}
    for _ in range(N_ITER):
        idx = rng.integers(0, n, size=n)
        boot = point.iloc[idx]
        for col in samples:
            samples[col].append(boot[col].median())

    print("=" * 70)
    print(f"Bootstrap CI (n_iter={N_ITER}, resample today-days, point=median)")
    print("=" * 70)
    rows_ci = []
    for col, arr in samples.items():
        a = np.array(arr)
        med = float(np.median(a))
        lo, hi = np.percentile(a, [2.5, 97.5])
        width = hi - lo
        rows_ci.append({"score": col, "median": med, "ci_lo": lo, "ci_hi": hi, "ci_width": width})

    ci_df = pd.DataFrame(rows_ci).round(2)
    print(ci_df.to_string(index=False))

    # вердикт
    health_width = float(ci_df.loc[ci_df["score"] == "health", "ci_width"].iloc[0])
    print(f"\nhealth CI width: {health_width:.2f}")
    verdict = "PASS" if health_width <= 5.0 else "FAIL"
    print(f"VERDICT: {verdict}  (порог ≤5 пунктов)")
    if verdict == "FAIL":
        print("  → метрика слишком шумная на n=61; на меньших окнах ещё хуже")
        print("  → варианты: расширить baseline window (>90 дней), использовать EWMA, day-of-week нормировка")

    out = {
        "n_iter": N_ITER,
        "n_today_days": int(n),
        "point_stats": point.describe().to_dict(),
        "bootstrap_ci": rows_ci,
        "verdict": verdict,
        "health_ci_width": health_width,
    }
    out_path = OUT_DIR / "health_bootstrap_ci.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False, default=str)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

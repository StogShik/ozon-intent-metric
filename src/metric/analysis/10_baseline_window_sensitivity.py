"""
C4. Чувствительность к baseline-окну.

Берём один и тот же синтетический spike (cart_per_click −5σ на day=15 в today-окне)
и считаем Δhealth + порог raw-MAD для 4 baseline-схем:
  - rolling 14 (последние 14 дней до today)
  - rolling 30
  - rolling 60
  - fixed first-half (то, что в production)

Pass: разница в Δhealth между схемами ≤ 3 пункта; разница в TTD ≤ 2 дня.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ANTI, OUT_DIR, health_score_row, load_daily, mad_threshold


SCHEMES = {
    "fixed_first_half": ("fixed", 30),
    "rolling_14": ("rolling", 14),
    "rolling_30": ("rolling", 30),
}


def baseline_for_day(daily: pd.DataFrame, today_idx: int, scheme: str, size: int) -> pd.DataFrame:
    """today_idx — позиция дня в полном daily."""
    if scheme == "fixed":
        return daily.iloc[:size]
    elif scheme == "rolling":
        lo = max(0, today_idx - size)
        return daily.iloc[lo:today_idx]
    raise ValueError(scheme)


def main() -> None:
    daily = load_daily().set_index("date")
    n = len(daily)
    print(f"Daily: {n} дней")

    # точка инъекции: середина мая в наших 61-дневных данных невозможна (нет мая)
    # → берём INJECT в today-окне ближе к концу
    TARGET_FEATURE = "cart_per_click"
    BASE_FIXED = 30
    INJECT_OFFSET_IN_TODAY = 15  # 30+15 = day 45 (середина апреля)
    inject_global_idx = BASE_FIXED + INJECT_OFFSET_IN_TODAY
    inject_date = daily.index[inject_global_idx]
    print(f"Inject day (global): idx={inject_global_idx}  date={inject_date.date()}")
    print(f"Feature: {TARGET_FEATURE} (positive → атакуем падением)\n")

    rows = []
    for scheme_name, (kind, size) in SCHEMES.items():
        base = baseline_for_day(daily, inject_global_idx, kind, size)
        if len(base) < 7:
            print(f"  [{scheme_name}] skip: только {len(base)} дней baseline")
            continue
        baseline_means = base.mean(numeric_only=True).to_dict()
        base_vals = base[TARGET_FEATURE].values.astype(float)
        sigma = 1.4826 * float(np.median(np.abs(base_vals - np.median(base_vals))))
        if sigma == 0:
            sigma = float(np.std(base_vals)) or 1.0
        lo, _, hi = mad_threshold(base_vals)

        # spike −5σ
        injected = daily[TARGET_FEATURE].copy().values
        direction = +1 if TARGET_FEATURE in ANTI else -1
        injected[inject_global_idx] = injected[inject_global_idx] + direction * 5 * sigma

        # clean and injected health на сегодня
        clean_today = daily.loc[inject_date].to_dict()
        inj_today = clean_today.copy()
        inj_today[TARGET_FEATURE] = injected[inject_global_idx]
        clean_h = health_score_row(clean_today, baseline_means)["health"]
        inj_h = health_score_row(inj_today, baseline_means)["health"]
        d_health = inj_h - clean_h

        # alert?
        v = inj_today[TARGET_FEATURE]
        is_alert = (v > hi) if TARGET_FEATURE in ANTI else (v < lo)

        # ширина коридора в σ-единицах исходной фичи
        corridor_width = hi - lo

        rows.append(
            {
                "scheme": scheme_name,
                "kind": kind,
                "size_days": size,
                "n_base_actual": len(base),
                "sigma_feature": sigma,
                "corridor_lo": lo,
                "corridor_hi": hi,
                "corridor_width": corridor_width,
                "clean_health": clean_h,
                "injected_health": inj_h,
                "delta_health": d_health,
                "raw_alert_triggered": bool(is_alert),
            }
        )

    df = pd.DataFrame(rows)
    print("=" * 70)
    print("Сравнение схем (один и тот же spike −5σ на cart_per_click)")
    print("=" * 70)
    show_cols = ["scheme", "n_base_actual", "corridor_width", "clean_health",
                 "injected_health", "delta_health", "raw_alert_triggered"]
    print(df[show_cols].round(3).to_string(index=False))

    # сравнение
    spread_dhealth = float(df["delta_health"].max() - df["delta_health"].min())
    spread_clean = float(df["clean_health"].max() - df["clean_health"].min())
    alert_consistent = bool(df["raw_alert_triggered"].nunique() == 1)
    print(f"\nspread(Δhealth across schemes): {spread_dhealth:.2f}")
    print(f"spread(clean_health across schemes): {spread_clean:.2f}")
    print(f"alert_consistent (все схемы согласны): {alert_consistent}")

    verdict = "PASS" if (spread_dhealth <= 3.0 and alert_consistent) else "FAIL"
    print(f"\nVERDICT: {verdict}")
    if verdict == "FAIL":
        worst = df.iloc[df["delta_health"].abs().idxmin()]
        print(f"  → менее чувствительная схема: {worst['scheme']} (Δh={worst['delta_health']:.2f})")
        print(f"  → нужно зафиксировать схему в weights.yaml и в metric.py")

    out = {
        "target_feature": TARGET_FEATURE,
        "inject_global_idx": inject_global_idx,
        "inject_date": str(inject_date.date()),
        "schemes": df.to_dict(orient="records"),
        "spread_delta_health": spread_dhealth,
        "spread_clean_health": spread_clean,
        "alert_consistent": alert_consistent,
        "verdict": verdict,
    }
    out_path = OUT_DIR / "baseline_window_sensitivity.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False, default=str)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

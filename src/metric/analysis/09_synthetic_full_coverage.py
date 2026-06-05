"""
C3. Синтетическая инъекция на всех 16 фичах × 3 сценария (spike/step/drift).

raw-MAD per-feature канал — production. Для каждой фичи мы знаем direction:
- anti (rise=bad): аномалия = +Δ (anti-направление)
- positive (drop=bad): аномалия = −Δ

Магнитуда нормируется в σ (1.4826·MAD): spike=+5σ, step=+3σ, drift=линейно 0→+5σ за 10 дней.
σ-нормировка нужна, потому что фичи имеют разные масштабы (rate vs km_median).

Для каждой комбинации (feature, scenario) считаем:
- detected: попал ли хоть один true-day в alerts
- latency: TTD (дней от inject_start до первого алерта)
- d_health: средний Δhealth на инъекционных днях

Pass: ≥80% (39/48) комбинаций detected, ни одна фича не «слепая» во всех 3 сценариях.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    ALL_FEATURES,
    ANTI,
    GROUPS,
    OUT_DIR,
    health_score_row,
    load_daily,
    mad_threshold,
)


SCENARIOS = ["spike", "step", "drift"]
INJECT_DAY = 10  # offset внутри today-окна
DRIFT_LEN = 10


def make_anomaly_direction(feature: str) -> int:
    """+1 = атакуем ростом (для anti); −1 = атакуем падением (для positive)."""
    return +1 if feature in ANTI else -1


def inject(values: np.ndarray, scenario: str, day: int, sigma: float, direction: int) -> tuple[np.ndarray, set[int]]:
    """Возвращает (injected_series, set_of_true_days)."""
    out = values.copy()
    if scenario == "spike":
        out[day] = out[day] + direction * 5 * sigma
        truth = {day}
    elif scenario == "step":
        out[day:] = out[day:] + direction * 3 * sigma
        truth = set(range(day, len(out)))
    elif scenario == "drift":
        for k in range(DRIFT_LEN):
            if day + k < len(out):
                # линейно растущая магнитуда 0 → 5σ за DRIFT_LEN дней
                magnitude = direction * 5 * sigma * (k + 1) / DRIFT_LEN
                out[day + k] = out[day + k] + magnitude
        truth = set(range(day, day + DRIFT_LEN))
    else:
        raise ValueError(scenario)
    return out, truth


def main() -> None:
    daily = load_daily().set_index("date")
    BASE = 30
    baseline = daily.iloc[:BASE]
    today = daily.iloc[BASE:].copy()
    n_today = len(today)
    baseline_means = baseline.mean(numeric_only=True).to_dict()
    print(f"Baseline: {BASE} дней,  Today: {n_today} дней")
    print(f"INJECT_DAY={INJECT_DAY},  DRIFT_LEN={DRIFT_LEN}\n")

    # baseline health stats (для Δhealth)
    base_health = [health_score_row(baseline.loc[d].to_dict(), baseline_means)["health"] for d in baseline.index]
    base_health_med = float(np.median(base_health))

    # точечный health на чистом today
    clean_health = pd.Series(
        [health_score_row(today.loc[d].to_dict(), baseline_means)["health"] for d in today.index],
        index=today.index,
    )

    results = []
    for feature in ALL_FEATURES:
        base_vals = baseline[feature].values.astype(float)
        lo, med, hi = mad_threshold(base_vals)
        sigma = 1.4826 * float(np.median(np.abs(base_vals - np.median(base_vals))))
        if sigma == 0:
            sigma = float(np.std(base_vals)) or 1.0  # fallback
        direction = make_anomaly_direction(feature)

        for scenario in SCENARIOS:
            today_vals = today[feature].values.astype(float)
            injected, truth = inject(today_vals, scenario, INJECT_DAY, sigma, direction)

            # raw-MAD алерты
            if feature in ANTI:
                alerts = np.where(injected > hi)[0].tolist()
            else:
                alerts = np.where(injected < lo)[0].tolist()
            alerts_set = set(alerts)
            tp = len(alerts_set & truth)
            fp = len(alerts_set - truth)
            fn = len(truth - alerts_set)
            precision = tp / (tp + fp) if (tp + fp) else float("nan")
            recall = tp / (tp + fn) if (tp + fn) else float("nan")
            # latency: первый alert ≥ INJECT_DAY
            future_alerts = sorted([a for a in alerts if a >= INJECT_DAY])
            ttd = (future_alerts[0] - INJECT_DAY) if future_alerts else None

            # Δhealth на truth-днях
            inj_today = today.copy()
            inj_today[feature] = injected
            inj_health = pd.Series(
                [health_score_row(inj_today.loc[d].to_dict(), baseline_means)["health"] for d in inj_today.index],
                index=inj_today.index,
            )
            truth_idx = sorted(truth)
            dh = float((inj_health.iloc[truth_idx] - clean_health.iloc[truth_idx]).mean())

            results.append(
                {
                    "feature": feature,
                    "scenario": scenario,
                    "is_anti": feature in ANTI,
                    "sigma": sigma,
                    "n_truth_days": len(truth),
                    "tp": tp,
                    "fp": fp,
                    "fn": fn,
                    "precision": precision,
                    "recall": recall,
                    "ttd": ttd,
                    "delta_health": dh,
                    "detected": ttd is not None,
                }
            )

    df = pd.DataFrame(results)
    print("=" * 70)
    print("РЕЗУЛЬТАТЫ: detected/latency/Δhealth")
    print("=" * 70)
    piv_detect = df.pivot(index="feature", columns="scenario", values="ttd")
    print("\nTTD per feature × scenario (None = НЕ задетектено):")
    print(piv_detect.to_string())

    piv_dh = df.pivot(index="feature", columns="scenario", values="delta_health").round(2)
    print("\nΔhealth on truth-days:")
    print(piv_dh.to_string())

    n_total = len(df)
    n_detected = int(df["detected"].sum())
    print(f"\nDetected: {n_detected}/{n_total} ({100*n_detected/n_total:.1f}%)")

    # слепые фичи (ни один сценарий не задетектен)
    blind = df.groupby("feature")["detected"].sum().reset_index()
    blind_features = blind[blind["detected"] == 0]["feature"].tolist()
    print(f"\nПолностью слепые фичи (0/3 сценариев): {blind_features}")

    # слабые фичи (1/3)
    weak = blind[blind["detected"] == 1]["feature"].tolist()
    print(f"Слабые (1/3): {weak}")

    # вердикт
    pct = n_detected / n_total
    verdict = "PASS" if (pct >= 0.80 and not blind_features) else "FAIL"
    print(f"\nVERDICT: {verdict}")
    if verdict == "FAIL":
        print("  → нужна перенастройка thresholds или замена raw-MAD на rolling-MAD/EWMA для слепых фичей")

    out = {
        "n_total": n_total,
        "n_detected": n_detected,
        "pct_detected": pct,
        "blind_features": blind_features,
        "weak_features": weak,
        "results": df.to_dict(orient="records"),
        "verdict": verdict,
    }
    out_path = OUT_DIR / "synthetic_full_coverage.json"
    with open(out_path, "w") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False, default=str)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

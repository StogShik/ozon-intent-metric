"""
C5. Сводный verdict по 4 проверкам (07/08/09/10) + рекомендации.

Читает JSON-артефакты из out/ и формирует validation_summary.json + текстовый
отчёт. Главная задача — назвать конкретные изменения, если что-то FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT_DIR


CHECKS = [
    ("C1_fp_rate_baseline_global", "fp_rate_baseline.json", "[diagnostic] FP rate global MAD LOO — мотивирует переход на DOW"),
    ("C1b_fp_rate_dow_loo", "fp_rate_dow.json", "FP rate adaptive DOW MAD LOO direction-aware"),
    ("C1c_fp_rate_dow_production", "fp_rate_dow_production.json", "FP rate adaptive DOW MAD в production-сетапе (baseline=30, today=31)"),
    ("C2_health_bootstrap_ci", "health_bootstrap_ci.json", "Bootstrap CI median(health) ≤5 пунктов"),
    ("C3_synthetic_full", "synthetic_full_coverage.json", "16 фичей × 3 сценария: ≥80% detected, нет слепых"),
    ("C4_baseline_window", "baseline_window_sensitivity.json", "Spread Δhealth между схемами baseline ≤3 пункта"),
]


def load(name: str) -> dict:
    p = OUT_DIR / name
    if not p.exists():
        return {"verdict": "MISSING"}
    with open(p) as fp:
        return json.load(fp)


def main() -> None:
    data = {name: load(fname) for name, fname, _ in CHECKS}

    print("=" * 72)
    print("VALIDATION SUMMARY (задача 3: bootstrap + synthetic + sensitivity)")
    print("=" * 72)
    print()

    all_pass = True
    recommendations: list[str] = []
    section_summaries: dict[str, dict] = {}

    # C1 (global) считается diagnostic, не блокирует
    BLOCKING = {n for n, _, _ in CHECKS if not n.startswith("C1_fp_rate_baseline_global")}

    for name, fname, desc in CHECKS:
        d = data[name]
        v = d.get("verdict", "MISSING")
        ok = v in ("PASS", "PASS_AT_3.5")
        if name in BLOCKING:
            all_pass &= ok
        mark = "✓ PASS" if ok else "✗ FAIL"
        if name not in BLOCKING:
            mark = "ⓘ DIAG"
        print(f"[{mark}] {name}")
        print(f"        {desc}")

        section = {"verdict": v, "blocking": name in BLOCKING}

        if name == "C1_fp_rate_baseline_global":
            fail3 = d.get("fail_count_k3", "?")
            fail35 = d.get("fail_count_k3_5", "?")
            print(f"        fail count: k=3 → {fail3}/16,  k=3.5 → {fail35}/16 (заменено adaptive DOW ниже)")
            section.update({"fail_k3": fail3, "fail_k3_5": fail35})

        elif name == "C1b_fp_rate_dow_loo":
            fail3 = d.get("fail_count_k3", "?")
            fail35 = d.get("fail_count_k3_5", "?")
            print(f"        LOO direction-aware: k=3 → {fail3}/16,  k=3.5 → {fail35}/16")
            section.update({"fail_k3": fail3, "fail_k3_5": fail35})
            if v not in ("PASS", "PASS_AT_3.5"):
                recommendations.append(
                    "C1b: LOO valид (n=61) даёт остаточный шум на cart_rate / session_conversion_rate "
                    "из-за тяжёлых хвостов конверсионных метрик. Перепрогнать при >90 днях данных."
                )

        elif name == "C1c_fp_rate_dow_production":
            fail3 = d.get("fail_count_k3", "?")
            fail35 = d.get("fail_count_k3_5", "?")
            print(f"        production (baseline=30, today=31): k=3 → {fail3}/16,  k=3.5 → {fail35}/16")
            section.update({"fail_k3": fail3, "fail_k3_5": fail35})
            if v == "PASS_AT_3.5":
                recommendations.append(
                    "C1c: использовать k=3.5 в production (default в anomaly.MadDetector.fit)."
                )
            elif v == "FAIL":
                recommendations.append(
                    "C1c: 3 growth-фичи (rate_views/clicks/favs) шумят на baseline=30 — "
                    "слишком мало точек per DOW (~4-5). Принять как known limitation; "
                    "перепрогнать при baseline≥60 дней."
                )

        elif name == "C2_health_bootstrap_ci":
            w = d.get("health_ci_width", None)
            print(f"        health CI width: {w}")
            section["health_ci_width"] = w

        elif name == "C3_synthetic_full":
            pct = d.get("pct_detected", 0)
            blind = d.get("blind_features", [])
            weak = d.get("weak_features", [])
            print(f"        detected: {pct:.0%}  blind={blind}  weak={weak}")
            section.update({"pct_detected": pct, "blind": blind, "weak": weak})
            if blind:
                recommendations.append(
                    f"C3: слепые фичи {blind} — пересмотреть thresholds или удалить из метрики."
                )
            if weak:
                recommendations.append(
                    f"C3: слабые фичи {weak} (только spike детектится) — добавить EWMA-канал "
                    "для step/drift или принять как trade-off."
                )

        elif name == "C4_baseline_window":
            sp = d.get("spread_delta_health", None)
            ac = d.get("alert_consistent", None)
            print(f"        spread Δhealth: {sp:.2f}  alert_consistent: {ac}")
            section.update({"spread_delta_health": sp, "alert_consistent": ac})

        section_summaries[name] = section
        print()

    print("=" * 72)
    overall = "PASS" if all_pass else "FAIL"
    print(f"OVERALL: {overall}")
    print("=" * 72)
    print()

    if not recommendations:
        print("Никаких изменений метрики не требуется. Готово к фиксации.")
    else:
        print("Что менять:")
        for i, r in enumerate(recommendations, 1):
            print(f"  {i}. {r}")

    summary = {
        "overall": overall,
        "sections": section_summaries,
        "recommendations": recommendations,
    }
    out_path = OUT_DIR / "validation_summary.json"
    with open(out_path, "w") as fp:
        json.dump(summary, fp, indent=2, ensure_ascii=False)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

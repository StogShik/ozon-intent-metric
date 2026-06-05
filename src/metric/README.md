# src/metric — Metric Science team

Production Health Score (day×category) + anomaly detection.

## Production

- `metric.py` — `load_weights`, `compute_metrics`, `health_score`, `metric_consensus`, `stratified_health`, `DeltaScoreDiagnostic`. Single source of truth — `configs/weights.yaml`.
- `anomaly.py` — `MadDetector` (adaptive DOW-deseasonalized raw-MAD, direction-aware, k=3.5).
- `framework.py` — `metricFramework` (init/load/new_day/history). Sprint 5, в процессе (см. roadmap).
- `_smoke_test.py` — `python -m metric._smoke_test` проверяет, что весь production-API живой.

## Analysis

`analysis/` — 13 нумерованных скриптов (задачи №2-3 веса/валидация). Каждый сохраняет JSON-результат в `analysis/out/`. См. `docs/metric_choice.md` и `docs/validation.md` за интерпретацией.

## Research (current)

- `new_metric.ipynb` — H-score исследования (текущее).
- `weight_calibration_consensus.ipynb`, `weight_calibration_delta.ipynb` — research по весам.

## Legacy (frozen)

- `create_intent_sessions_scored.py`, `metrics.ipynb` — старая session-level IRS метрика до пивота на Health Score. Не использовать в production.

## Координация

- **Pipeline**: получает `day_summary.parquet` через `CONTRACT_SUMMARY_OUTPUT`. Веса и формулы — внутри `src/metric/`.
- **NLP**: NLP-фичи (`complex_query_rate`, `in_product_base_rate`) пока pending — статус «не вошли в композит». См. M8 в roadmap.

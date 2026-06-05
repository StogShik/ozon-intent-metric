# src/pipeline — Pipeline / Data Engineering team

Поток `raw events → intent_sessions → day_summary → daily_metrics`.

## Production

- `build_intent_sessions.py` — `raw user_actions → intent_sessions.parquet` (sessionization, join product info, agg по `user_id × session_idx`).
- `run_daily_pipeline.py` — orchestrator (CLI). Поднимает sessions → day_summary → daily_metrics. Использует `src/metric/metric.py::health_score` для расчёта Health Score.
- `build_day_summary.py` — альтернативная production-сборка day_summary с lifelines KM-фитом и NLP-фичами (`complex_query_rate`, `in_product_base_rate`). **Внимание:** на текущей ветке import `build_chunk_lazy` ломается (function renamed → `build_intent_sessions_lazy`). См. TODO в файле.

## Запуск

См. корневой `README.md` секции «Полный расчёт» и «Быстрая проверка».

## Контракты

- `intent_sessions` v1 → `docs/schema.md`
- `day_summary` v1 → `docs/schema.md` + docstring `build_day_summary.py`

## Координация с другими командами

- **Metric**: потребляет `day_summary.parquet`. Контракт — `CONTRACT_SUMMARY_OUTPUT` в `build_day_summary.py`.
- **NLP**: `complex_query_rate`, `in_product_base_rate` требуют `data/query_features.parquet` от NLP team (см. `src/nlp/`).
- **DE notebooks**: `src/de/bot_filter.ipynb` — research для будущей фильтрации ботов на стадии sessionize.

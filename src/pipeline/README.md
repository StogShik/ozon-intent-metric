# src/pipeline — Pipeline / Data Engineering team

Поток `raw events · intent_sessions · day_summary · daily_metrics (+RCA-артефакты)`.

## Production

- `build_intent_sessions.py` — `raw user_actions · intent_sessions.parquet` (sessionization gap_min=35, join product info, agg по `user_id × session_idx`).
- `enrich_sessions_nlp.py` — NLP-обогащение sessions (query_clean, query_len_words, is_in_product_base, is_article, query_stratification_score) из каталога товаров; CLI с `--verify-against` для regression-сверки. Порт `research/post-stratification.ipynb`, сверен с эталоном бит-в-бит.
- `run_daily_pipeline.py` — orchestrator (CLI). За один прогон строит:
  - `day_summary` (inline-агрегация date×category, KM-медиана времени до корзины);
  - `daily_metrics` (Health Score raw/stratified/drift + alert-агрегаты) — через `src/metric/metric.py::health_score`;
  - `decomposition` (per-day RCA: группы/фичи/категории + anomaly-коридоры) — через `src/metric/rca.py::decompose_health`;
  - `mad_detector.json` (state детектора аномалий, fit на baseline);
  - `segment_breakdown` (structural RCA по NLP-срезам, период + per-day) — при переданном `--segments-sessions`.

Сломанный standalone `build_day_summary.py` (CLI с NLP-агрегатами) перенесён в `archive/pipeline/` — пригодится при включении NLP-фичей в композит.

## Запуск

См. корневой `README.md` секции «Полный расчёт» и «Быстрая проверка».

## Контракты

- `intent_sessions` v1 · `docs/schema.md`
- `day_summary` v1 · `docs/schema.md`
- `decomposition` / `segment_breakdown` · `docs/schema.md` (потребители: `src/web/server.py`)

## Координация с другими командами

- **Metric**: потребляет `day_summary`; формулы и веса — внутри `src/metric/`.
- **NLP**: `--segments-sessions data/intent_sessions_with_query_features.parquet` — артефакт NLP-команды (см. `src/nlp/README.md`).

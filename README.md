# ozon-intent-metric

Метрика качества поискового движка Ozon: **day×category Health Score** — ежедневный
индекс здоровья поиска относительно baseline. `100` = baseline, `<100` хуже,
`>100` лучше.

> Это **интегрированная** ветка с кодом всех команд. Команды-ветки
> `de` / `nlp` / `metric` оставлены как архив; новая работа — здесь и в `main`.

## Команды и зоны ответственности

| Команда | Папка | Что владеет | README |
|---|---|---|---|
| **Pipeline / DE** | `src/pipeline/`, `src/de/` | `raw events → intent_sessions → day_summary → daily_metrics` | [src/pipeline/README.md](src/pipeline/README.md) |
| **NLP** | `src/nlp/` | домен-словарь, нормализация, query_features (NLP-фичи запросов) | [src/nlp/README.md](src/nlp/README.md) |
| **Metric Science** | `src/metric/` | Health Score (`metric.py`), Anomaly (`anomaly.py`), Framework (`framework.py`), валидация (`analysis/`) | [src/metric/README.md](src/metric/README.md) |
| **Web UI** | `src/web/` | локальный сайт-просмотрщик `daily_metrics.parquet` | [src/web/README.md](src/web/README.md) |

Граница между Pipeline и Metric — контракт `CONTRACT_SUMMARY_OUTPUT` в
`src/pipeline/build_day_summary.py` (схема day_summary). Кто что меняет за
этой границей — внутреннее дело команды.

## Поток данных

```
raw user_actions               (data/user_actions_3_months/)
        │
        ▼   src/pipeline/build_intent_sessions.py
intent_sessions                 (data/intent_sessions_full.parquet)
        │
        ▼   + src/nlp/query_features_table.py
intent_sessions + NLP-features  (data/intent_sessions_with_query_features.parquet)
        │
        ▼   src/pipeline/build_day_summary.py  (или inline в run_daily_pipeline.py)
day_summary (date × category)   (data/day_summary_full.parquet)
        │
        ▼   src/metric/metric.py::health_score
daily_metrics (Health Score)    (data/daily_metrics_full.parquet)
        │
        ▼   src/web/server.py
HTTP UI on :8760
```

## Установка

Linux / macOS / WSL:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Входные данные (не в git)

```
data/user_actions_3_months/   # hive-partitioned raw events (~10M событий/день)
data/product_information/     # каталог товаров (130k)
```

См. `.gitignore` — все большие parquet'ы (`intent_sessions_*`, `day_summary_*`,
`daily_metrics_*`, `query_features`, и т.п.) **не коммитятся**, пересобирайте
локально командами ниже.

## Полный расчёт

Период: `2024-03-01 … 2024-04-30`. Baseline для Health Score: `2024-03-01 … 2024-03-30`.

```bash
python src/pipeline/run_daily_pipeline.py \
  --start 2024-03-01 --end 2024-04-30 \
  --sessions-out data/intent_sessions_full.parquet \
  --day-summary-out data/day_summary_full.parquet \
  --daily-metrics-out data/daily_metrics_full.parquet \
  --baseline-start 2024-03-01 --baseline-end 2024-03-30 \
  --min-category-sessions 2000 --km-min-events 30 --session-chunk-days 1
```

Если `intent_sessions_full.parquet` уже собран — переиспользуем:

```bash
python src/pipeline/run_daily_pipeline.py \
  --in-sessions data/intent_sessions_full.parquet \
  --day-summary-out data/day_summary_full.parquet \
  --daily-metrics-out data/daily_metrics_full.parquet \
  --baseline-start 2024-03-01 --baseline-end 2024-03-30
```

## Быстрая проверка (6 дней)

```bash
python src/pipeline/run_daily_pipeline.py \
  --start 2024-03-05 --end 2024-03-10 \
  --sessions-out data/intent_sessions_week_test.parquet \
  --day-summary-out data/day_summary_week_test.parquet \
  --daily-metrics-out data/daily_metrics_week_test.parquet \
  --baseline-start 2024-03-05 --baseline-end 2024-03-10 \
  --min-category-sessions 50 --km-min-events 5 --session-chunk-days 1
```

## Web UI

```bash
python src/web/server.py --metrics data/daily_metrics_full.parquet --port 8760
# открыть http://127.0.0.1:8760
```

## Конфигурация Health Score

`configs/weights.yaml` — **single source of truth** для весов и направлений
фичей. Обоснование — `docs/metric_choice.md`. Валидация — `docs/validation.md`.

```python
from metric.metric import load_weights, health_score
weights = load_weights()
result = health_score(today_df, baseline_df, weights)
result.health, result.stratified, result.drift_signal
```

## Документация

- `docs/schema.md` — контракты `intent_sessions` и `day_summary`.
- `docs/metric_candidates.md` — 5 кандидатов в метрику (legacy bake-off).
- `docs/metric_choice.md` — обоснование выбора Health Score, Equal-весов.
- `docs/validation.md` — C1–C5 проверки (FP rate, bootstrap CI, synthetic).
- `docs/weight_calibration.md` — методология подбора весов.
- `docs/session_threshold.md` — выбор `gap_min` для сессионизации.
- `docs/day_summary_beta.md` — заметки DE-команды.

## Git-ветки

- `main` — стабильная интегрированная версия (мёрджится из `pipeline`).
- `pipeline` — интеграционная ветка (где этот README).
- `de`, `nlp`, `metric` — **архивные** ветки команд. Не использовать для новой работы.
- `backup-pre-integration-*` — снапшот перед интеграцией веток.

Новые фичи → ветка от `main` → PR в `main`.

## Заметки

- Generated parquet-файлы в `data/` не коммитим (`.gitignore`).
- Для больших периодов используется `--session-chunk-days 1`, чтобы снизить
  RAM. Если упёрлись в память — `--session-chunk-days 1` обязательно.
- Если порт занят — `--port 8761` или другой.

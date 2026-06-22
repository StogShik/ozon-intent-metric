# ozon-intent-metric

Метрика качества поискового движка Ozon: **day×category Health Score** — ежедневный
индекс здоровья поиска относительно baseline (`100` = baseline, `<100` хуже,
`>100` лучше) **плюс RCA-слой**, отвечающий на вопрос «почему просело»:
декомпозиция по фичам/группам/категориям, anomaly-детектор и слабые сегменты
по NLP-срезам.

## Сценарий аналитика

1. **Увидеть просадку** — Overview: timeline Health Score, красные точки = дни
   с MAD-алертом, sidebar аномальных дней.
2. **Понять, какая метрика/категория утянула** — Day detail: аддитивная
   декомпозиция дня (Σ contribution по фичам = Health), алерты с коридорами
   детектора (±σ), слабые категории.
3. **Сравнить с нормальным днём** — Δ explain: разложение ΔHealth между двумя
   датами по группам/фичам/категориям.
4. **Найти сегмент-виновника** — Weak spots за конкретный день: какие NLP-срезы
   (сложность запроса, миссы по каталогу, длина, виджет входа…) просели против
   своей нормы за период; ранжирование по «лишним» провалам.
5. **Посмотреть живые примеры** — Examples: реальные провальные сессии сегмента
   за этот день (запрос, уточнения, виджет).

## Структура репозитория

| Папка | Что это | README |
|---|---|---|
| `src/pipeline/` | production: raw events, sessions, day_summary, daily_metrics + RCA-артефакты | [README](src/pipeline/README.md) |
| `src/metric/` | production: Health Score, anomaly, RCA | — |
| `src/web/` | дашборд аналитика (HTTP + ES-modules frontend) | [README](src/web/README.md) |
| `configs/` | `weights.yaml` — single source of truth весов | — |
| `docs/` | [находки](docs/findings.md), обоснование метрики, валидация, справочник по данным | — |
| `tests/` | pytest-сетка инвариантов (синтетика, без `data/`) | — |
| `research/` | research-ноутбуки (обоснование решений) | — |

## Поток данных

```
raw user_actions               (data/user_actions_3_months/)
        │
        ▼   src/pipeline/build_intent_sessions.py
intent_sessions                 (data/intent_sessions_full.parquet)
        │
        ▼   src/pipeline/enrich_sessions_nlp.py
intent_sessions + NLP-features  (data/intent_sessions_with_query_features.parquet)
        │
        ▼   src/pipeline/run_daily_pipeline.py
        ├── day_summary (date × category)     data/day_summary_full.parquet
        ├── daily_metrics (Health Score)      data/daily_metrics_full.parquet
        ├── decomposition (per-day RCA)       data/decomposition_full.parquet
        ├── MadDetector state                 data/framework_state/mad_detector.json
        └── segment_breakdown (weak spots)    data/segment_breakdown.parquet
        │
        ▼   src/web/server.py
HTTP UI on :8080
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

## Проверки

```bash
make test           # pytest: инварианты метрики/RCA/детектора/NLP-порта, <5 сек, data/ не нужен
make smoke          # smoke-test production-API на синтетике
make verify-enrich  # сверка NLP-порта с готовым артефактом (нужен data/)
```

Остальные цели в `Makefile`: `enrich`, `pipeline`, `ui`, `all`.

## Входные данные (не в git)

```
data/user_actions_3_months/   # hive-partitioned raw events (~10M событий/день)
data/product_information/     # каталог товаров (130k)
```

См. `.gitignore` — все большие parquet'ы (`intent_sessions_*`, `day_summary_*`,
`daily_metrics_*`, `decomposition_*`, `segment_breakdown*`, `query_features`)
**не коммитятся**, пересобирайте локально командами ниже.

## Полный расчёт

Период: `2024-03-01 … 2024-04-30`. Baseline для Health Score: `2024-03-01 … 2024-03-30`.

```bash
python src/pipeline/run_daily_pipeline.py \
  --start 2024-03-01 --end 2024-04-30 \
  --sessions-out data/intent_sessions_full.parquet \
  --day-summary-out data/day_summary_full.parquet \
  --daily-metrics-out data/daily_metrics_full.parquet \
  --baseline-start 2024-03-01 --baseline-end 2024-03-30 \
  --segments-sessions data/intent_sessions_with_query_features.parquet \
  --min-category-sessions 2000 --km-min-events 30 --session-chunk-days 1
```

`--segments-sessions` включает сборку weak-spots (structural RCA). Сам артефакт
собирается так (один раз после пересборки sessions):

```bash
python src/pipeline/enrich_sessions_nlp.py \
  --sessions data/intent_sessions_full.parquet \
  --products data/product_information \
  --out data/intent_sessions_with_query_features.parquet
```

Без него дашборд работает, но вкладки Weak spots / Examples будут пустыми.

Если `intent_sessions_full.parquet` уже собран — переиспользуем:

```bash
python src/pipeline/run_daily_pipeline.py \
  --in-sessions data/intent_sessions_full.parquet \
  --day-summary-out data/day_summary_full.parquet \
  --daily-metrics-out data/daily_metrics_full.parquet \
  --baseline-start 2024-03-01 --baseline-end 2024-03-30 \
  --segments-sessions data/intent_sessions_with_query_features.parquet
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

## Web UI (дашборд аналитика)

```bash
make ui   # = python src/web/server.py --metrics data/daily_metrics_full.parquet --port 8080
# открыть http://127.0.0.1:8080
```

Пути к decomposition / segment_breakdown / sessions подхватываются по
умолчанию из `data/`; переопределяются флагами `--decomposition`,
`--segments`, `--sessions`.

## Конфигурация Health Score

`configs/weights.yaml` — **single source of truth** для весов и направлений
фичей. Обоснование — `docs/metric_choice.md`. Валидация — `docs/validation.md`.

```python
from metric.metric import load_weights, health_score
weights = load_weights()
result = health_score(today_df, baseline_df, weights)
result.health, result.stratified, result.drift_signal
```

RCA programmatic API — `src/metric/rca.py` (`decompose_health`, `explain_drop`,
`segment_day_view`, `example_failures`); HTTP-обёртки над ним — `src/web/server.py`
(`/api/day`, `/api/explain`, `/api/weak-spots?date=…`, `/api/examples`).

## Документация

- `docs/findings.md` — что не так с поиском Ozon: находки и топ-3 фикса.
- `docs/metric_choice.md` — обоснование выбора Health Score и весов.
- `docs/validation.md` — проверки метрики (FP rate, bootstrap CI, synthetic).
- `docs/session_threshold.md` — выбор `gap_min` для сессионизации.
- `docs/schema.md` — справочник по данным (`intent_sessions`, `day_summary`, `decomposition`, `segment_breakdown`).

## Git-ветки

- `main` — основная ветка.

Новые фичи: ветка от `main`, затем PR в `main`.

## Заметки

- Generated parquet-файлы в `data/` не коммитим (`.gitignore`).
- Для больших периодов используется `--session-chunk-days 1`, чтобы снизить RAM.
- Если порт занят — `--port 8761` или другой.

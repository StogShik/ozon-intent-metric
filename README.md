# ozon-intent-metric

Рабочая версия для предзащиты.

Проект считает дневную метрику качества поиска для кейса "скорость реализации
интента": сколько усилий требуется пользователю, чтобы дойти до целевого
действия (`to_cart`), и как меняется общий `Health Score` по дням.

## Что делает пайплайн

```text
raw user_actions
  -> intent_sessions
  -> day_summary
  -> daily_metrics
  -> web UI
```

Основные артефакты:

- `data/intent_sessions_full.parquet` — intent-сессии пользователей.
- `data/day_summary_full.parquet` — дневная витрина по категориям.
- `data/daily_metrics_full.parquet` — итоговые дневные метрики для сайта.

В итоговой таблице `data/daily_metrics_full.parquet` есть:

- `health_score`
- `stratified_health_score`
- `mean_searches_to_cart`
- `mean_unique_queries_to_cart`
- `mean_time_to_cart_s`
- `n_sessions`
- `converted_sessions`

## Установка окружения проекта

Linux / WSL:

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

## Входные данные

Для полного расчета нужны:

```text
data/user_actions_3_months/
data/product_information/
```

## Полный расчет

Пайплайн поддерживает два режима:

1. Полный расчет из raw logs (`--start`, `--end`).
2. Пересчет витрин из уже готового `data/intent_sessions_full.parquet` (`--in-sessions`).

Период данных: `2024-03-01` ... `2024-04-30`.

Baseline для `Health Score`: `2024-03-01` ... `2024-03-30`.

### 1. Полный расчет из raw logs

```bash
python src/pipeline/run_daily_pipeline.py \
  --start 2024-03-01 \
  --end 2024-04-30 \
  --sessions-out data/intent_sessions_full.parquet \
  --day-summary-out data/day_summary_full.parquet \
  --daily-metrics-out data/daily_metrics_full.parquet \
  --baseline-start 2024-03-01 \
  --baseline-end 2024-03-30 \
  --min-category-sessions 2000 \
  --km-min-events 30 \
  --session-chunk-days 1
```

### 2. Пересчет из готового `data/intent_sessions_full.parquet`

Если `data/intent_sessions_full.parquet` уже построен (остался с прошлого запуска
или был создан отдельной командой), можно пересчитать только витрины и метрики.

Отдельно построить только `data/intent_sessions_full.parquet`:

```bash
python src/pipeline/build_intent_sessions.py \
  --start 2024-03-01 \
  --end 2024-04-30 \
  --input data/user_actions_3_months \
  --products data/product_information \
  --out data/intent_sessions_full.parquet \
  --gap-min 35 \
  --chunk-days 1
```

Пересчитать витрины и метрики из готового `data/intent_sessions_full.parquet`:

```bash
python src/pipeline/run_daily_pipeline.py \
  --in-sessions data/intent_sessions_full.parquet \
  --day-summary-out data/day_summary_full.parquet \
  --daily-metrics-out data/daily_metrics_full.parquet \
  --baseline-start 2024-03-01 \
  --baseline-end 2024-03-30 \
  --min-category-sessions 2000 \
  --km-min-events 30
```

## Быстрая проверка пайплайна

Эта команда считает только несколько дней и нужна для проверки запуска без ожидания полного периода.

```bash
python src/pipeline/run_daily_pipeline.py \
  --start 2024-03-05 \
  --end 2024-03-10 \
  --sessions-out data/intent_sessions_week_test.parquet \
  --day-summary-out data/day_summary_week_test.parquet \
  --daily-metrics-out data/daily_metrics_week_test.parquet \
  --baseline-start 2024-03-05 \
  --baseline-end 2024-03-10 \
  --min-category-sessions 50 \
  --km-min-events 5 \
  --session-chunk-days 1
```

## Web UI

Запуск сайта на полном расчете:

```bash
python src/web/server.py \
  --metrics data/daily_metrics_full.parquet \
  --port 8760
```

Открыть:

```text
http://127.0.0.1:8760
```

Запуск сайта на тестовом недельном расчете:

```bash
python src/web/server.py \
  --metrics data/daily_metrics_week_test.parquet \
  --port 8760
```

## Структура кода

- `src/pipeline/build_intent_sessions.py` — построение intent-сессий из raw logs.
- `src/pipeline/health_metric.py` — расчет `Health Score`.
- `src/pipeline/run_daily_pipeline.py` — основной entrypoint пайплайна.
- `src/web/server.py` — локальный HTTP-сервер.
- `src/web/index.html`, `src/web/app.js`, `src/web/styles.css` — интерфейс.
- `configs/weights.yaml` — веса групп и фичей для `Health Score`.

## Примечания

- Generated parquet-файлы в `data/` не рекомендуется коммитить в Git.
- Для больших периодов используется `--session-chunk-days 1`, чтобы снизить
  потребление памяти.
- Если порт занят, запустите сайт на другом порту, например `--port 8761`.

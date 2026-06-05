# build_day_summary

Скрипт `src/pipeline/build_day_summary.py` выполняет два шага:
1. Построение intent-сессий из сырых событий (`build_sessions`)
2. Агрегация сессий в дневные сводки по категориям (`build_daily_category_summary`)

---

## Шаг 1: build_sessions

Строит единый `intent_sessions.parquet` из hive-партиционированных событий, обрабатывая данные чанками по несколько дней.

**Поведение при существующем файле:**
Если файл уже есть, построение пропускается. Чтобы принудительно пересобрать — используй флаг `--rewrite`.

**Алгоритм:**
1. Период `[start_d, end_d]` нарезается на чанки по `chunk_days` дней.
2. Для каждого чанка вызывается `build_chunk_lazy` (из `build_intent_sessions`), результат пишется в отдельный шард `*.parts/part_NNN.parquet`.
3. Если `sink_parquet` падает — делается fallback на `collect(engine='streaming')`.
4. После обработки всех чанков шарды мерджатся в итоговый файл через `pl.scan_parquet(...).sink_parquet(out_path)`.
5. Временные шарды удаляются (если не передан `--keep-shards`).

**Параметры:**

| Параметр | По умолчанию | Описание |
|---|---|---|
| `events_root` | `data/user_actions_3_months` | Корень hive-партиционированных событий |
| `products_root` | `data/product_information` | Папка с product_information parquet |
| `start_d` / `end_d` | — | Период включительно (YYYY-MM-DD) |
| `gap_min` | `30` | Порог неактивности в минутах для разбиения сессий |
| `chunk_days` | `7` | Размер чанка в днях |
| `keep_shards` | `False` | Сохранить промежуточные шарды после merge |
| `rewrite` | `False` | Перезаписать уже существующий файл сессий |
| `out_path` | `data/intent_sessions.parquet` | Путь к итоговому файлу |

---

## Шаг 2: build_daily_category_summary

Агрегирует intent-сессии в дневные срезы по товарным категориям. Работает memory-efficient: в памяти держится только один день за раз.

**Алгоритм:**

1. **Классификация категорий** — один проход по всему LazyFrame:
   Категории с суммарным числом сессий >= 500 за весь период остаются как есть. Все остальные (включая `null`) заменяются на `'other'`.

2. **Агрегация день за днём** — итерация по уникальным датам:
   - Фильтрация LazyFrame на текущую дату
   - Применение маппинга категорий
   - `group_by(['date', 'category'])` + агрегация метрик
   - Запись в `day_summary_YYYY_MM_DD.parquet`
   - Освобождение памяти (`del`, `gc.collect()`)

**Выходные колонки на строку `(date, category)`:**

| Колонка | Описание |
|---|---|
| `n_users` | Уникальных пользователей за день |
| `n_sessions` | Всего сессий |
| `rate_events` | Среднее n_events на сессию |
| `rate_searches` | Среднее n_search на сессию |
| `rate_views` | Среднее n_view на сессию |
| `rate_clicks` | Среднее n_click на сессию |
| `rate_carts` | Среднее n_cart на сессию |
| `rate_favs` | Среднее n_fav на сессию |
| `rate_unique_queries` | Среднее n_unique_queries на сессию |
| `is_cart` | Число сессий, достигших события корзины |

**Выходные файлы:** `<out_dir>/day_summary_YYYY_MM_DD.parquet` — по одному на каждый день в данных.

---

## Контракт данных

### Вход: `intent_sessions.parquet`

Колонки, обязательные для `build_daily_category_summary`. Зафиксированы в `CONTRACT_SESSIONS_INPUT`.

| Колонка | Тип Polars | Ограничения |
|---|---|---|
| `ts_end` | `Datetime` (любой tz) | не null |
| `user_id` | любой Int | — |
| `top_category_in_session` | `Utf8` | nullable → маппится в `'other'` |
| `n_events` | любой Int | >= 0 |
| `n_search` | любой Int | >= 0 |
| `n_view` | любой Int | >= 0 |
| `n_click` | любой Int | >= 0 |
| `n_cart` | любой Int | >= 0 |
| `n_fav` | любой Int | >= 0 |
| `n_unique_queries` | любой Int | >= 0 |
| `reached_cart` | `Boolean` | не null |

Валидация запускается автоматически при старте `build_daily_category_summary` через `validate_sessions_schema()`.
При нарушении — немедленный `ValueError` с перечислением всех несоответствий.

### Выход: `day_summary_YYYY_MM_DD.parquet`

Одна строка = `(date, category)`. Зафиксировано в `CONTRACT_SUMMARY_OUTPUT`.

| Колонка | Тип Polars | Гарантии |
|---|---|---|
| `date` | `Date` | — |
| `category` | `Utf8` | из фиксированного списка топ-N или `'other'` |
| `n_users` | `UInt32` | >= 1 |
| `n_sessions` | `UInt32` | >= 1 |
| `rate_events` | `Float64` | >= 0 |
| `rate_searches` | `Float64` | >= 0 |
| `rate_views` | `Float64` | >= 0 |
| `rate_clicks` | `Float64` | >= 0 |
| `rate_carts` | `Float64` | >= 0 |
| `rate_favs` | `Float64` | >= 0 |
| `rate_unique_queries` | `Float64` | >= 0 |
| `is_cart` | `Int32` | 0 <= is_cart <= n_sessions |

Строки внутри файла отсортированы по `category`. Имя файла: `day_summary_YYYY_MM_DD.parquet`.

---

## CLI

```bash
# Построить сессии из сырых данных + дневная сводка
python build_day_summary.py \
  --start 2024-01-01 --end 2024-03-31 \
  --input data/user_actions_3_months \
  --products data/product_information \
  --out-sessions data/intent_sessions.parquet \
  --out-dir data/daily_summaries

# Пересобрать сессии (даже если файл уже есть)
python build_day_summary.py \
  --start 2024-01-01 --end 2024-03-31 \
  --rewrite

# Использовать готовый файл сессий, пересчитать только один день
python build_day_summary.py \
  --in-sessions data/intent_sessions.parquet \
  --target-date 2024-02-15 \
  --out-dir data/daily_summaries
```

**Все флаги:**

| Флаг | Описание |
|---|---|
| `--start` / `--end` | Период для построения сессий (взаимоисключает `--in-sessions`) |
| `--in-sessions` | Готовый parquet с сессиями (пропустить построение) |
| `--input` | Корень событий (default: `data/user_actions_3_months`) |
| `--products` | Папка product_information (default: `data/product_information`) |
| `--gap-min` | Порог неактивности в минутах (default: `30`) |
| `--chunk-days` | Размер чанка (default: `7`) |
| `--keep-shards` | Сохранить шарды после merge |
| `--rewrite` | Перезаписать существующий файл сессий |
| `--out-sessions` | Путь к итоговому файлу сессий (default: `data/intent_sessions.parquet`) |
| `--out-dir` | Папка дневных сводок (default: `data/daily_summaries`) |
| `--target-date` | Пересчитать только один день (YYYY-MM-DD) |

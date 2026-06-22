# Справочник по данным

Этот документ описывает схемы данных, на которых строится решение: состав
колонок, типы, единицы и инварианты. Здесь: `intent_sessions`, `day_summary`,
RCA-артефакты (`decomposition`, `segment_breakdown`).

---

# Схема `intent_sessions`

Версия: **v1** (2026-04-26)

`intent_sessions` — единая таблица интент-сессий (sessions с ≥1 `search`),
основной артефакт сессионизации и точка входа для всех downstream-расчётов
(метрика, RCA, аналитика).

---

## Источник

- Скрипт: `src/pipeline/build_intent_sessions.py`
- Входы:
  - `data/user_actions_3_months/` (hive `date=*/action_type=*/*.parquet`)
  - `data/product_information/*.parquet` (для `brand`/`category_name`)
- Параметры пайплайна (CLI):
  - `--start`, `--end` — закрытый интервал дат (YYYY-MM-DD)
  - `--gap-min` (default `30`) — порог неактивности, формирующий границу сессии
  - `--chunk-days` (default `7`) — размер чанка обработки
- Артефакт: `data/intent_sessions_full.parquet` (для полного периода)
  или `data/intent_sessions_*.parquet` для срезов.

## Гранулярность и первичный ключ

- 1 строка = 1 интент-сессия пользователя.
- **PK**: `(user_id, session_idx)`. `session_idx` — счётчик сессий в пределах
  пользователя, нумерация с 1, монотонная по времени.
- Сессия = непрерывная последовательность событий одного `user_id`,
  где соседние события разделены не более чем `--gap-min` минутами.
- В таблицу попадают **только сессии с `n_search ≥ 1`** (отсюда «intent»).
  Сессии без поискового события из этой таблицы исключены.

---

## Колонки

| # | Колонка | Polars dtype | Nullable | Единицы | Описание |
|---|---|---|---|---|---|
| 1 | `user_id` | `Int32` | нет | — | Идентификатор пользователя из source-логов. |
| 2 | `session_idx` | `Int32` | нет | — | Индекс сессии внутри `user_id`, ≥ 1. |
| 3 | `ts_start` | `Datetime[ns]` | нет | UTC-naive ns | Время первого события сессии. |
| 4 | `ts_end` | `Datetime[ns]` | нет | UTC-naive ns | Время последнего события сессии. |
| 5 | `n_events` | `UInt32` | нет | шт. | Полное число событий в сессии. |
| 6 | `n_search` | `UInt32` | нет | шт. | Число событий с `action_type = search`. ≥ 1 по построению. |
| 7 | `n_view` | `UInt32` | нет | шт. | Число событий `view`. |
| 8 | `n_click` | `UInt32` | нет | шт. | Число событий `click`. |
| 9 | `n_cart` | `UInt32` | нет | шт. | Число событий `to_cart`. |
| 10 | `n_fav` | `UInt32` | нет | шт. | Число событий `favorite`. |
| 11 | `n_unique_queries` | `UInt32` | нет | шт. | Число различных непустых `search_query` в сессии (Query-Depth кандидат). |
| 12 | `first_query` | `String` | да¹ | — | Первый ненулевой `search_query` (последовательность по `timestamp`). |
| 13 | `first_widget` | `String` | да¹ | — | `widget_name` поискового события, давшего `first_query`. |
| 14 | `n_unique_categories` | `UInt32` | нет | шт. | Число различных `category_name` среди товаров, с которыми было взаимодействие в сессии (после join с `product_information` по `item_id`). Сигнал «category drift». |
| 15 | `top_category_in_session` | `String` | да² | — | Модальная `category_name` сессии (по событиям с непустым `item_id`). |
| 16 | `duration_s` | `Int64` | нет | секунды | `ts_end − ts_start` в секундах. ≥ 0. |
| 17 | `reached_cart` | `Boolean` | нет | — | `True` ⇔ `n_cart > 0`. Прокси успешной реализации интента. |
| 18 | `ts_first_cart` | `Datetime[ns]` | да³ | UTC-naive ns | Время первого события `to_cart`. `null` для незавершённых сессий. |
| 19 | `time_to_cart_s` | `Int64` | да³ | секунды | `ts_first_cart − ts_start` в секундах. `null` для незавершённых сессий. ≥ 0. |

**Сноски о nullability:**

- ¹ `first_query` / `first_widget` — формально могут быть `null`, если все
  поисковые события сессии содержат `null` в соответствующем поле. На
  актуальных данных встречается крайне редко.
- ² `top_category_in_session` = `null`, если сессия не содержит ни одного
  события с известным `item_id` (чисто «search-only»/«zero-interaction»
  сессия). На периоде 2024-03-01 … 2024-04-30 — ≈ **5.1%** сессий.
  Этот случай сам по себе — диагностический сигнал поиска (zero-result).
- ³ `ts_first_cart` и `time_to_cart_s` оба `null` тогда и только тогда,
  когда `reached_cart == False`.

---

## Инварианты (должны выполняться на любом валидном артефакте)

1. **Уникальность ключа**: `(user_id, session_idx)` уникален.
2. **Intent-фильтр**: `n_search ≥ 1` для всех строк.
3. **Сумма счётчиков**: `n_events == n_search + n_view + n_click + n_cart + n_fav`.
4. **Порядок времени**: `ts_start ≤ ts_end`, `duration_s ≥ 0`.
5. **Целевое действие**: `reached_cart == (n_cart > 0)`.
6. **Связь cart-полей**:
   - `ts_first_cart IS NOT NULL` ⇔ `reached_cart == True`
   - `time_to_cart_s IS NOT NULL` ⇔ `reached_cart == True`
   - При `reached_cart == True`: `ts_start ≤ ts_first_cart ≤ ts_end`,
     `0 ≤ time_to_cart_s ≤ duration_s`.
7. **Категории**: `n_unique_categories ≥ 1` ⇔ `top_category_in_session IS NOT NULL`.
8. **Уникальные запросы**: `1 ≤ n_unique_queries ≤ n_search`.

Любое нарушение инварианта — это баг пайплайна, а не валидное состояние данных.

---

## Известные ограничения

- **Сессии режутся на границах чанков обработки** (`--chunk-days`).
  Сессия пользователя, идущая с 23:55 одного чанка до 00:30 следующего, будет
  разбита на две. На дефолтной нарезке по неделям таких граничных сессий
  единицы на тысячу. Для более точной оценки `duration_s` / `time_to_cart_s`
  на хвостах — гонять с `--chunk-days` ≥ длины периода (требует больше RAM).
- **`category_name` источника содержит ~89 `null` строк** на 130 035 товаров и
  ~227 `null` `brand`. Эти `null`'ы протекают в события и могут попадать в
  `top_category_in_session` только если **все** товары сессии без категории —
  тогда колонка станет `null`, что эквивалентно «zero-interaction» кейсу выше.
- **Атрибуция запроса к событию** (`active_query` внутри `ev`) — last-touch
  forward-fill `search_query` внутри сессии. На границы сессии заглушка
  не выходит. В таблицу `intent_sessions` сама `active_query` не агрегируется
  (используется downstream в `query_features`).

---

# Схема `day_summary` (v1)

**Производит:** inline-агрегация в `src/pipeline/run_daily_pipeline.py::build_day_summary`.
**Потребляет:** `src/metric/metric.py::compute_metrics / health_score`,
`src/metric/rca.py::decompose_health`.

Гранулярность: одна строка = `(date, category)`. `category` — категории с
≥ `--min-category-sessions` (default 2000) сессий за период, остальное · `'other'`.
Сессия относится к дню её `ts_end`.

| Колонка | Тип | Описание |
|---|---|---|
| `date` | Date | день (`ts_end.date()`) |
| `category` | Utf8 | top-категория или `'other'` |
| `n_users`, `n_sessions` | UInt32 | уникальные пользователи / сессии |
| `rate_events`, `rate_searches`, `rate_views`, `rate_clicks`, `rate_favs`, `rate_unique_queries` | Float64 | mean(n_*) на сессию |
| `is_cart` | Int32 | sum(reached_cart) |
| `cart_rate` | Float64 | mean(n_cart) |
| `session_conversion_rate` | Float64 | mean(reached_cart) |
| `bounce_rate` | Float64 | mean(n_events == 1) |
| `dead_search_rate` | Float64 | mean(n_click == 0) |
| `search_refinement_rate` | Float64 | mean(n_unique_queries > 1) |
| `mean_duration_s` | Float64 | mean(duration_s) |
| `multi_category_rate` | Float64 | mean(n_unique_categories > 1) |
| `view_per_search`, `click_per_view`, `cart_per_click` | Float64 | funnel-отношения sum/sum; null при нулевом знаменателе |
| `mean_searches_to_cart`, `mean_unique_queries_to_cart`, `mean_time_to_cart_s` | Float64 | mean по успешным сессиям |
| `km_median_time_to_cart_s` | Float64 | Kaplan-Meier медиана времени до корзины (цензурирование по duration_s); null если < `--km-min-events` событий |

NLP-агрегатов `complex_query_rate`, `in_product_base_rate` в текущем выходе
`day_summary` нет.

---

# Схемы RCA-артефактов

## `decomposition` (long-format, per-day RCA)

**Производит:** `src/metric/rca.py::decompose_health` (вызывается из
`run_daily_pipeline.py`). **Потребляет:** `src/web/server.py` (`/api/day`,
`/api/explain`, `/api/anomalies`).

Одна строка = `(date, level, entity)`, `level ∈ {group, feature, category}`.
Полный список колонок — `DECOMP_COLUMNS` в `rca.py`. Инварианты:

- `Σ contribution[level=feature] ≈ health_score (raw)` этого дня;
- `Σ contribution[level=group] ≈ health_score (raw)`;
- `Σ contribution[level=category, included=True] ≈ stratified_health_score`.

Нарушение инвариантов валит pipeline AssertionError'ом (рассинхрон
decomposition ↔ daily_metrics).

Feature-строки дополнительно несут anomaly-контекст детектора:
`is_anomaly`, `dow_used`, `residual`, `sigma`, `corridor_lo`, `corridor_hi`.

## `segment_breakdown` (structural RCA)

**Производит:** `src/metric/rca.py::precompute_all_segments(include_daily=True)`.
**Потребляет:** `src/web/server.py` (`/api/weak-spots`, `/api/segments`).

Одна строка = `(segment, value, date)`, где `date = "ALL"` (агрегат периода)
или `"YYYY-MM-DD"` (per-day строки — для «какой сегмент просел именно в этот
день»). Колонки: `n_sessions`, `n_success`, `success_rate`, `share`,
`lift_vs_overall` (vs overall того же дня/периода), `failure_volume`,
`median_n_queries`, `median_n_views`, `median_time_s`.



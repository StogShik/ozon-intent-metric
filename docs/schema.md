# Контракт схемы `intent_sessions`

Версия: **v1** (2026-04-26)

`intent_sessions` — единая таблица интент-сессий (sessions с ≥1 `search`),
основной артефакт роли Data Engineer и точка входа для всех downstream-ролей
(Metric Scientist, ML Researcher, Analyst).

Этот документ — **контракт**. Любое downstream-решение строится против него,
а любое изменение колонок/типов/инвариантов — это bump версии и обновление
этого файла.

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



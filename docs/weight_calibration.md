# Распределение весов в Health Score через consensus-подход

Двухуровневая схема: статистика взвешивает однородные сигналы внутри групп, бизнес — разнородные оси между группами.

## Состав групп и распределение фичей

Health Score состоит из 3 групп с фиксированными доменными весами `0.40 / 0.35 / 0.25`. Внутри каждой группы — взвешенное среднее фичей через consensus-метод. Семантически каждая группа отвечает за **отдельную ось качества движка**.

### Quality (вес 0.40) — качество поиска и пути до корзины

Отвечает на вопрос: **«насколько движок помогает пользователю быстро найти и купить?»**. Фичи отражают где интент пользователя застревает или фрустрирует.

| Фича | Direction | Семантика |
|---|---|---|
| `dead_search_rate` | ↓ | `P(no click \| n_search > 0)` — поиск без клика, фрустрация |
| `search_refinement_rate` | ↓ | `P(>1 query \| n_search > 0)` — пользователь переформулирует запрос |
| `bounce_rate` | ↓ | `mean(n_events == 1)` — однособытийные сессии, мгновенный отказ |
| `km_median_time_to_cart_s` | ↓ | KM-медиана времени до корзины (с цензурированием 52% сессий без корзины) |
| `complex_query_rate` | ↓ | доля сессий с `query_stratification_score >= 4` (NLP, см. ниже) |
| `in_product_base_rate` | ↑ | доля first-query, точно совпадающих с product name/brand/type/category (NLP) |

**Источник NLP-фичей:** `src/nlp/post-stratification.ipynb` обогащает session-level данные полями `query_stratification_score` (0–5, complexity score) и `is_in_product_base` (bool). Эти поля затем агрегируются в day_summary как rate-метрики per (date, category).

**Метод:** `OptMin`. Группа гетерогенна — `km_median` ортогональна остальным (corr -0.13 с `dead_search`), NLP-фичи могут оказаться отдельной осью «понятность интента». OptMin защищает каждую ось от обнуления.

---

### Engagement (вес 0.35) — глубина взаимодействия

Отвечает на вопрос: **«насколько вовлечены пользователи?»**. От базовых счётчиков активности до конверсионных сигналов.

| Фича | Direction | Семантика |
|---|---|---|
| `rate_clicks` | ↑ | `mean(n_click)` per session |
| `rate_views` | ↑ | `mean(n_view)` per session |
| `rate_favs` | ↑ | `mean(n_fav)` per session |
| `multi_category_rate` | ↑ | `mean(n_unique_categories > 1)` — сессии с несколькими категориями |
| `click_per_view` | ↑ | `sum(n_click) / sum(n_view)` — funnel transition (relevance ranking) |
| `cart_per_click` | ↑ | `sum(n_cart) / sum(n_click)` — funnel transition (buying intent) |
| `cart_rate` | ↑ | `mean(n_cart)` per session — event-based интенсивность добавлений |
| `session_conversion_rate` | ↑ | `sum(reached_cart) / sum(n_sessions)` — binary конверсия сессий |

**Метод:** под вопросом. Базовые `rate_*` сильно коррелированы (corr 0.7–0.95) → PC1 хорош. Но funnel-ratios (`click_per_view`, `cart_per_click`) и conversion-сигналы (`cart_rate`, `session_conversion_rate`) могут образовать **отдельный кластер**, не подыгрывающий общему PC1. **Решение:** прогнать и PC1, и OptMin, выбрать по cross-validation стабильности.

---

### Discovery (вес 0.25) — активность поиска

Отвечает на вопрос: **«насколько активно пользователи исследуют каталог?»**.

| Фича | Direction | Семантика |
|---|---|---|
| `rate_unique_queries` | ↑ | `mean(n_unique_queries)` — разнообразие запросов |
| `rate_searches` | ↑ | `mean(n_search)` — частота поиска |
| `view_per_search` | ↑ | `sum(n_view) / sum(n_search)` — funnel transition (search effectiveness) |

**Метод:** `Equal` — 3 фичи слишком мало для устойчивого Optim, базовые две сильно коррелированы.

---

### Что **вне** Health Score (но рядом, как отдельные индикаторы)

- `mean_duration_s` — двусмысленное направление (длинная сессия = engaged или frustrated?)
- `mean_query_complexity` (mean от score 0–5) — redundant с `complex_query_rate` (доля высоких скоров), оставили только rate-версию
- `mean_query_len_words` — амбивалентное направление (короткий = простой / длинный = специфичный)
- `Effort` (из `src/de/bot_filter.ipynb` 1.1) — это **bot-filter сигнал**, не health-индикатор; в Health Score не идёт

---

### Что было раньше и почему изменилось

| Изменение | Было | Стало | Причина |
|---|---|---|---|
| `cart_rate` event-based | `mean(reached_cart)` (binary) | `mean(n_cart)` (event-based) | согласовано с другими `rate_*`; binary ушло в `session_conversion_rate` |
| `rate_carts` | `mean(n_cart)`, отдельная колонка | удалена | дубликат нового `cart_rate` |
| `cart_rate` / `session_conversion_rate` | вне Health Score (как «таргеты» supervised калибровки) | в engagement | переход на consensus = не нужен таргет, циркулярность ушла |
| `search_refinement_rate` | anti-метрика (флаг `−1`) | quality, всё ещё `↓` (anti) | corr +0.60 с `cart_rate` означает что **уточнение запроса** — это engaged behavior, но в quality оно отражает что **поиск с первого раза не помог** |
| NLP-фичи | отсутствовали | `complex_query_rate`, `in_product_base_rate` в quality | команда NLP добавила post-stratification per session |
| Funnel transitions | отсутствовали | `view_per_search`, `click_per_view`, `cart_per_click` распределены по группам | декомпозиция воронки даёт инвариантные к объёму трафика сигналы |

---

## Общий принцип

```
WITHIN-group:  consensus на фичах группы → веса w_q1..w_q4, w_e1..w_e4, w_d1, w_d2
BETWEEN-group: consensus на group_scores (3 временных ряда) ИЛИ domain priors
                                        → веса w_q, w_e, w_d
```

Сначала фиксируем within (нижний уровень), потом вычисляем `group_scores`, на них считаем between. Bottom-up.

Итоговый вес фичи в Health Score:
```
final_weight(feature) = w_group × w_within_group
например: final_weight(km_median) = 0.40 × OptMin_share ≈ 0.40 × 0.22 = 0.088
```

---

## Внутри групп: выбор метода

Метод выбирается **под структуру данных конкретной группы**, не один на всех:

| Свойство группы | Метод | Почему |
|---|---|---|
| Много фичей (≥4), сильно коррелированы | **PC1** | Доминирующий общий фактор → loadings отражают силу сигнала |
| Много фичей, есть ортогональные | **OptMin** | Гарантирует ненулевой вес каждой → защищает ортогональные |
| Мало фичей (2-3) | **Equal** | Optim на 2-3 точках = шум, проще равные |
| Не уверен | **OptMin** | Самый «справедливый» дефолт |

### Применение к нашим группам

| Группа | Фичи | Структура | Рекомендуемый метод |
|---|---|---|---|
| **quality** (6) | dead_search ↓, search_refinement ↓, bounce ↓, km_median ↓, complex_query_rate ↓, in_product_base_rate ↑ | смешанная: dead_search/refinement/bounce коррелированы; km_median ортогональна; NLP-фичи — отдельная ось «понятность интента» | **OptMin** — защитить разнородные оси |
| **engagement** (8) | rate_clicks, rate_views, rate_favs, multi_category, click_per_view, cart_per_click, cart_rate, session_conversion_rate | базовые `rate_*` сильно коррелированы; ratio-фичи (`*_per_*`) могут отколоться от общего фактора; conversion-сигналы — частично свой кластер | **проверить оба**: PC1 если общий фактор силён, иначе OptMin |
| **discovery** (3) | rate_unique_queries, rate_searches, view_per_search | 3 фичи, высокая взаимная corr | **Equal** — Optim на 3 точках = шум |

См. раздел [Состав групп](#состав-групп-и-распределение-фичей) для детальных описаний и направлений каждой фичи.

---

## Между группами: тонкости

Группы — это **3 временных ряда** group_score за baseline. Три проблемы:

### Проблема 1: всего 3 «фичи»
PC1 на 3 точках доминирует и забирает 80–90% дисперсии. Optim на 3 переменных гипер-чувствителен к малым изменениям.

### Проблема 2: сильная коллинеарность по конструкции
Все три group_score движутся вверх когда системе хорошо. Cross-corr между group_scores ≈ 0.6–0.9. Consensus → почти равные веса (1/3, 1/3, 1/3).

### Проблема 3: domain priors задуманы статистически нейтральными
`0.40/0.35/0.25` — продуктовое решение «поиск важнее discovery». Заменяя на consensus, теряем экспертизу.

### Варианты решения

| Вариант | Между группами | Когда подходит |
|---|---|---|
| **A. Hybrid (рекомендуется)** | domain priors `0.40/0.35/0.25` + consensus как diagnostic | Production. Если diagnostic сильно расходится с domain → пересмотр приоритетов |
| **B. Чистый consensus** | OptMin на 3 group_scores → ≈ `0.33/0.33/0.33` | Если нет product-input или хочется единой методологии |
| **C. Constrained consensus** | Optim с границами: `quality ∈ [0.30, 0.50]`, `engagement ∈ [0.25, 0.45]`, `discovery ∈ [0.15, 0.35]` | Best of both, но границы тоже надо обосновать |

---

## Полная схема (рекомендуемая)

```
within(quality)    = OptMin ([dead_search ↓, search_refinement ↓, bounce ↓,
                              km_median ↓, complex_query_rate ↓, in_product_base_rate ↑])

within(engagement) = PC1 OR OptMin (выбрать после CV)
                            ([rate_clicks, rate_views, rate_favs, multi_category,
                              click_per_view, cart_per_click,
                              cart_rate, session_conversion_rate])

within(discovery)  = Equal ([rate_unique_queries, rate_searches, view_per_search])

between            = domain ([quality:0.40, engagement:0.35, discovery:0.25])
                     + consensus как sanity check
```

---

## Правила нормировки

В каждом наборе веса должны:
- быть **неотрицательными** (фичи уже ориентированы по +1/−1)
- суммироваться в 1 (нормировка `w / w.sum()`)
- нести смысл «доли вклада»

---

## Валидация устойчивости весов

После выбора метода — проверить стабильность.

### 1. Cross-validation по времени
- Разбить baseline на первые 30 и последние 30 дней
- Прогнать consensus на каждой половине отдельно
- Сравнить веса: если максимальная разница > 30% относительной → метод нестабилен → fallback на Equal

### 2. Bootstrap по дням
- Ресэмплировать дни с возвратом 100 раз
- Считать веса для каждого ресэмпла
- Получить 95% CI для каждого веса
- Если CI шире чем сами веса → нестабильно

### 3. Sensitivity к удалению фичи
- Удалить по одной фиче, прогнать consensus, посмотреть как меняются веса остальных
- Если удаление одной фичи сильно меняет другие → сильная коллинеарность, метод нестабилен

---

## Sanity checks **после** интеграции

После пересчёта Health Score с новыми весами:

- `corr(Health_old, Health_new)` на baseline > 0.85 — не ломаем общий сигнал, только взвешиваем
- `Δalert_count` в норме ±20%
- `feature_contribution` — никакая фича не доминирует (вклад > 40% от total)

---

## Возможные конфигурации

| Сценарий | Within | Between |
|---|---|---|
| Безопасный дефолт | Equal everywhere | Equal (1/3) |
| Текущий (до интеграции) | Equal (`np.mean`) | Domain (0.40/0.35/0.25) |
| **Рекомендуемый** | OptMin/PC1/Equal по группе | Domain + consensus diagnostic |
| Чистый consensus | OptMin everywhere | OptMin |
| Адаптивный | пересчитывать ежемесячно | пересчитывать раз в квартал |

---

## TL;DR

- **Внутри групп:** consensus метод выбирается **под структуру данных группы** (PC1 для коррелированных, OptMin для смешанных, Equal для малых)
- **Между группами:** consensus как чистый метод **слабо подходит** (мало переменных, сильная коллинеарность, теряются доменные приоритеты)
- **Рекомендация:** hybrid — domain priors в production, consensus как diagnostic
- **Принцип:** статистика взвешивает однородное, бизнес — разнородное

---

## Coverage status

Сверка с реальным выходом `build_day_summary.py` (ветка `de`, 22 колонки в parquet):

| Группа | В Health Score | В pipeline сейчас | NLP TODO | Реальные пропуски |
|---|---|---|---|---|
| quality | 6 | 4 | 2 (`complex_query_rate`, `in_product_base_rate`) | 0 |
| engagement | 8 | 8 | — | 0 |
| discovery | 3 | 3 | — | 0 |
| **Итого** | **17** | **15** (✅ 88%) | **2** (явный gap) | **0** |

**Не входят в Health Score, но присутствуют как контекст** (для CI / MDE / диагностики):
- `date`, `category`, `n_users`, `n_sessions`, `is_cart`, `rate_events`, `mean_duration_s`

**Pipeline → docs соответствие:** 15 implemented + 2 explicit-TODO = **100% accounted for**. Реальных пропусков нет.

### Чтобы закрыть NLP-pending

`complex_query_rate` и `in_product_base_rate` ждут расширения `CONTRACT_SESSIONS_INPUT` полями `query_stratification_score` и `is_in_product_base` от `src/nlp/post-stratification.ipynb`. Раскомментировать в `CONTRACT_SUMMARY_OUTPUT` и добавить в агрегацию (см. `docs/day_summary_beta.md` секцию Pending).

---

## Роль weight_calibration_delta: Leave-One-Out validation

`weight_calibration_delta.ipynb` — **не источник production-весов**, а supervised validation для consensus.

### Проблема циркулярности и её решение

После того как `cart_rate` и `session_conversion_rate` были включены в Health Score (engagement), а `km_median_time_to_cart_s` — в quality, **наивная** supervised-калибровка с этими таргетами стала циркулярной: модель предсказывает свой собственный компонент.

**Решение — Leave-One-Out (LOO)**: для каждого таргета `T`:
1. Из набора фичей **исключаем** ту, что является источником таргета (или сам таргет, если он напрямую фича)
2. На LOO-наборе делаем supervised-калибровку (Ridge / PLS / CCA / Composite) на дельтах
3. Считаем `Δscore_loo = ΔX_loo @ w_loo` и проверяем `corr(Δscore_loo, ΔT)` на хвостовом окне
4. Сравниваем `w_loo` с **consensus-весами** на тех же LOO-фичах

LOO убирает прямой канал «таргет в фичах» и делает supervised-калибровку методологически корректной.

### Маппинг таргет → исключаемая фича

| Таргет | Из какой фичи происходит | Исключаем из X |
|---|---|---|
| `cart_rate` (event-based) | сама фича в engagement | `cart_rate` |
| `session_conversion_rate` (binary) | сама фича в engagement | `session_conversion_rate` |
| `km_speed_to_cart = 1 / km_median` | трансформация `km_median_time_to_cart_s` из quality | `km_median_time_to_cart_s` |

После LOO остаётся 14 фичей (15 − 1) для каждой калибровки.

### Что даёт каждый канал LOO

#### Канал 1: Сравнение supervised-весов с consensus-весами

```
1. Берём consensus-веса на LOO-наборе фичей: w_consensus_loo
2. Калибруем supervised на тех же фичах: w_supervised_loo (Ridge / PLS / CCA / Composite)
3. Сравниваем: cos_sim(w_consensus_loo, w_supervised_loo) или поэлементная разница
4. Высокое сходство → consensus согласован с supervised-сигналом, метрика устойчива
5. Сильное расхождение → consensus и supervised оптимизируют разное, повод разобраться
```

#### Канал 2: Hold-out предсказательная сила

```
1. Калибруем w_loo на baseline-окне
2. На held-out окне (последние 20 дней) считаем Δscore_loo и ΔT
3. corr(Δscore_loo, ΔT) → честная оценка предсказательной силы
4. Это не circular: ΔT не входит в Δscore_loo by construction
```

#### Канал 3: Structural input (наблюдение, не калибровка)

Delta-анализ выявил **анти-корреляцию** Δcart_rate ↔ Δkm_speed (corr ≈ −0.25 на хвосте). Это **наблюдение в данных**, а не результат supervised-калибровки — стоит независимо от методологии delta.

Полезно как **обоснование структурного решения**:
> `cart_rate` (engagement) и `km_speed_to_cart` / `km_median` (quality) — разные оси качества. Анти-корреляция доказала что нельзя их усреднять в одну composite-метрику.

**Методологическое правило для расширения:** при добавлении новой фичи в группу проверять её delta-correlation с другими фичами группы. Стабильная анти-корреляция → принадлежит другой группе или отдельной оси.

### Какие методы внутри LOO

PLS / CCA / Ridge / Composite — все одинаково применимы внутри LOO-схемы. **Циркулярность не зависит от метода**, она зависит от выбора таргета. После исключения таргетной фичи все 4 метода дают валидный supervised-сигнал, и их можно сравнивать между собой как обычно.

### Резюме разделения ролей

| Аспект | consensus | delta (LOO) |
|---|---|---|
| Что производит | production-веса для Health Score | validation + diagnostic |
| Парадигма | unsupervised, на уровнях | supervised на дельтах с LOO |
| Что валидирует | стат-согласованность фичей | предсказательную силу score без таргета |
| Как используется | source-of-truth для производства | second opinion на стабильность |
| Куда смотреть | `weight_calibration_consensus.ipynb` | `weight_calibration_delta.ipynb` |

Delta **поверх** consensus-весов даёт supervised-проверку без жертвы покрытия Health Score (`cart_rate` остаётся в скоре, валидация работает в LOO-режиме).

---

## Связанные артефакты

### Pipeline и метрика
- `src/pipeline/build_day_summary.py` — pipeline, источник `data/daily_summaries/`. Сюда нужно добавить новые фичи: `complex_query_rate`, `in_product_base_rate`, `view_per_search`, `click_per_view`, `cart_per_click`, `session_conversion_rate`
- `src/metric/new_metric.ipynb` — где веса интегрируются в Health Score (P1: вынести в `src/metric/health_score.py`)

### Калибровка весов
- `src/metric/weight_calibration_consensus.ipynb` — расчёт consensus-весов; **active**, source production-весов
- `src/metric/weight_calibration_delta.ipynb` — research-инструмент для оценки совместимости таргетов (`Δscore` vs `Δcart_rate`); не источник весов
- `src/metric/weight_calibration.ipynb` — **deprecated** (level-based supervised, циркулярная логика)

### NLP-обогащение (от коллег, ветка `nlp`)
- `src/nlp/post-stratification.ipynb` — производит per-session фичи: `query_stratification_score` (0–5), `is_in_product_base`, `is_article`, `query_len_words`. Источник для quality-фичей `complex_query_rate`, `in_product_base_rate`
- `src/nlp/normalize_query.py`, `build_domain_vocab.py`, `create_query_embeddings.py` — pipeline нормализации запросов и построения domain vocabulary
- `data/query_features.parquet` — query-level фичи (77 МБ); используется в post-stratification

### Bot-фильтрация (отдельный поток, не в Health Score)
- `src/de/bot_filter.ipynb` — research-ноутбук от коллег + раздел 1.4 (session-level Vol×Conv филтр); решение: **не интегрировать в pipeline**, метрика делается bot-robust by design


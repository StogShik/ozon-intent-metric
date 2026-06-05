# Выбор метрики и весов Health Score

> Сводный документ задачи №2: тестирование подходов, формирование весов, вердикт по delta.
>
> **Артефакты:** `configs/weights.yaml`, `src/metric/analysis/out/*.json`
> **Скрипты:** `src/metric/analysis/02_resolve_conflicts.py` … `06_synthetic_injection.py`
> **Baseline данных:** 61 день (2024-03-01 → 2024-04-30), агрегация по `n_sessions`-weighted mean.

## TL;DR

| Решение | Что выбрано | Почему |
|---|---|---|
| Within-group method | **Equal** во всех 3 группах | PC1/OptMin не прошли bootstrap (max_rel_CI до 121k у OptMin engagement) |
| Between-group weights | **0.40 / 0.35 / 0.25** (domain) | Consensus diagnostic расходится на 0.21 — flag «escalate» как known limitation |
| Direction `search_refinement_rate` | **+1** в engagement | Δ-corr с cart_rate = +0.71, с dead_search = −0.54 — эмпирически engagement |
| Direction `mean_duration_s` | **+1** в engagement | Oriented Δ-corr с engagement = +0.55 (сильнейший) |
| Delta как anomaly detector | **НЕ production** | На 5 синтетических сценариях raw-MAD доминирует; delta слепо к step/drift |

---

## §A0. Разрешение конфликтов конфига

Запуск: `python3 src/metric/analysis/02_resolve_conflicts.py`.

### A0.1. `search_refinement_rate` direction

Конфликт между ноутбуком (`+1`, engagement) и доками (`−1`, quality). Решено эмпирически — Δ-корреляции с reference-сигналами:

| Reference | Δ-corr | Trend |
|---|---|---|
| cart_rate | **+0.71** | refinement co-moves с конверсией |
| session_conversion_rate | +0.49 | то же, но слабее |
| dead_search_rate | **−0.54** | refinement обратно-связан с фрустрацией |
| bounce_rate | −0.20 | refinement обратно-связан с отказом |

**Вывод:** уточнение запроса — engagement-сигнал, не quality-сигнал. Direction = `+1`, группа = engagement.

Дока `docs/weight_calibration.md` (раздел quality + changelog «было anti, стало quality `↓`») в этой части **эмпирически опровергнута на baseline данных** и должна быть обновлена.

### A0.2. `mean_duration_s` инклюзия

Конфликт: ноутбук `+1` engagement, дока «вне Health Score из-за двусмысленного направления».

Oriented Δ-corr со средним по группе:

| Группа | mean_oriented_delta_corr | n_refs |
|---|---|---|
| quality | 0.164 | 4 |
| **engagement** | **0.545** | 4 |
| discovery | 0.381 | 3 |

**Вывод:** `mean_duration_s` чётко вписывается в engagement с direction `+1`.

### A0.3. Финальный 3-групповой состав

После A0.1 и A0.2 — 16 фичей (без NLP-pending):

```
quality      (3)  = dead_search_rate ↓, bounce_rate ↓, km_median_time_to_cart_s ↓
engagement   (10) = rate_clicks, rate_views, rate_favs, multi_category_rate,
                    click_per_view, cart_per_click, cart_rate, session_conversion_rate,
                    mean_duration_s, search_refinement_rate         (все ↑)
discovery    (3)  = rate_unique_queries, rate_searches, view_per_search   (все ↑)
```

`mean_duration_s` перенесён из «вне» в engagement; `search_refinement_rate` — из quality в engagement; направление поменялось.

---

## §A1–A3. Within-group consensus (per group)

Запуск: `python3 src/metric/analysis/03_within_group_weights.py`. На каждой группе считаются Equal / PC1 / OptMin на z-нормализованных ориентированных фичах. Стабильность — три проверки:

1. **Bootstrap по дням, 1000 ресэмплов** — 95% CI на каждый вес. Метрика: `max_rel_CI_width = ci_width / |median|`.
2. **Time-split CV** — первая половина (30 дней) vs вторая (31 день). Метрика: `max_abs_diff` между весами на половинах.
3. **Sensitivity к удалению фичи** — выкидываем по одной фиче, смотрим, как меняются веса остальных.

### Сводная таблица

| Группа | PC1 expvar | PC1 all positive? | min_corr (Eq/PC1/Opt) | Bootstrap PC1 max_rel_CI | Bootstrap OptMin max_rel_CI | Time-split max_diff (PC1/Opt) | Verdict |
|---|---|---|---|---|---|---|---|
| quality | 57.0% | 2/3 (km = 0.008) | 0.47 / −0.01 / 0.68 | 1.53 | 2.63 | 0.20 / 0.37 | **Equal** |
| engagement | <50% | 9/10 (click_per_view loading = −0.09) | −0.09 / −0.34 / 0.31 | 2.04 | **121418** | 0.11 / 0.38 | **Equal** |
| discovery | 66.1% | 2/3 (view_per_search loading = −0.08) | 0.42 / −0.08 / 0.68 | 2.31 | 34.66 | 0.08 / 0.50 | **Equal** |

### Decision rule (применяется per group)

```
if PC1.expvar > 0.50 AND все loadings > 0 AND PC1 bootstrap-stable:
    → PC1
elif bootstrap CI PC1 включают equal-веса (1/k) для каждой фичи:
    → Equal (consensus не делает работы)
elif OptMin.min_corr > Equal.min_corr + 0.10 AND OptMin bootstrap-stable:
    → OptMin
else:
    → Equal
```

### Почему Equal во всех трёх

- **quality.** PC1 expvar 57% — выше порога, но `km_median_time_to_cart_s` orthogonal (loading ≈ 0). PC1 фактически 2-фичевый индекс. OptMin даёт km median 0.46, повышая min_corr с 0.47 → 0.68, но bootstrap CI на km median = [0.02, 0.78] — практически случайный.
- **engagement.** PC1 expvar ниже 50%, click_per_view loading отрицательный. OptMin концентрирует вес на 3 фичах (click_per_view, multi_category, cart_per_click) и обнуляет 6 остальных — это противоположность «consensus». Bootstrap max_rel_CI = 121k подтверждает: на других днях OptMin выберет другие фичи.
- **discovery.** Аналогично: `view_per_search` имеет отрицательный loading в PC1; n=61 на 3 фичи — слишком мало для устойчивой оптимизации.

### Вывод

Equal — **не от лени, а от данных**: на baseline 61 день consensus-методы не дают надёжного преимущества над равными весами. Это решение полностью соответствует «безопасному дефолту» из `docs/weight_calibration.md`.

---

## §A4–A5. Between-group решение и sanity

Запуск: `python3 src/metric/analysis/04_between_group_and_sanity.py`.

### A4. Domain priors + consensus diagnostic

Считаем group_scores на `today_window` (последние 31 день) при baseline = первые 30 дней. Затем OptMin на 3 group_scores:

| Группа | Domain prior | Consensus OptMin | Δ |
|---|---|---|---|
| quality | 0.40 | **0.50** | +0.10 |
| engagement | 0.35 | **0.14** | −0.21 |
| discovery | 0.25 | **0.36** | +0.11 |

max |Δ| = 0.21 → **выше порога 0.10**.

### Почему расхождение

Inter-group correlations:

```
            quality  engagement  discovery
quality        1.00        0.24       0.23
engagement     0.24        1.00       0.98
discovery      0.23        0.98       1.00
```

**Engagement и discovery — 98% корреляция**, фактически одна ось. Quality — настоящая вторая ось (corr 0.23–0.24 со остальными). OptMin это видит и хочет дать quality больше веса (0.50), а engagement+discovery — поделить «вторую ось» на двоих.

### Решение

Domain priors (0.40/0.35/0.25) **сохраняем как production**. Расхождение фиксируем как **known limitation**:

> Группы engagement (10 фичей) и discovery (3 фичи) сильно redundant на уровне групповых score. В roadmap — рассмотреть консолидацию: либо объединить в одну группу «activity», либо переработать discovery (другие фичи, не rate-based).

### A5. Sanity-checks

#### A5.1. Преемственность с прежним np.mean
При within=Equal новая и старая формулы тождественны (`np.mean` ≡ Equal). Corr = 1.000. Проверка преемственности — формальная.

#### A5.2. Stress-test: +20% к `dead_search_rate` на 5 дней
Идеальное поведение: просесть должна только quality-группа.

| Метрика | Mean Δ за 5 дней stress |
|---|---|
| quality_score | **−6.73** |
| engagement_score | 0.00 |
| discovery_score | 0.00 |
| health_score | **−2.69** (≈ 0.40 × −6.73 ✓) |

**Изоляция групп подтверждена.** Stress в одной группе не «заражает» остальные.

#### A5.3. Feature dominance
Equal-веса по построению дают каждой фиче 1/k от group_score → автоматически ≤ 1/3 (quality, discovery) или 1/10 (engagement). Никаких фичей-доминаторов, требование `< 40%` выполняется тривиально.

---

## §B. Delta как anomaly detector

### B1. Hold-out fix

В `weight_calibration_delta.ipynb` веса фитились на ВСЁМ Δ-датасете, а corr считалась на последних 20 днях — это не hold-out, а **in-sample на хвосте**. Чинит `05_delta_anomaly_score.py`:

```
train = delta[:-14]  # 46 точек
val   = delta[-14:]  # 14 точек
# StandardScaler.fit ТОЛЬКО на train, веса фитим ТОЛЬКО на train
# corr(δ_score_val, target_val) — теперь честно
```

### B2. Hold-out corrs (corr(δ_score_val, Δtarget_val))

| Метод | Δcart_rate | Δsession_conv | Δkm_speed | mean\|corr\| |
|---|---|---|---|---|
| Ridge[cart_rate] | **+0.991** | +0.927 | **−0.263** | 0.727 |
| Ridge[session_conv] | +0.957 | **+0.988** | **−0.408** | 0.785 |
| Ridge[km_speed] | +0.154 | +0.361 | **−0.200** | 0.238 |
| PLS | +0.964 | +0.954 | **−0.360** | 0.759 |
| CCA | +0.990 | +0.960 | **−0.303** | 0.751 |
| Composite | +0.960 | +0.987 | **−0.372** | 0.773 |

**Структурный вывод:** на dead-honest hold-out все методы дают **отрицательную корреляцию с Δkm_speed** при положительной с Δcart_rate / Δsession_conv. Это та самая анти-корреляция `Δcart ↔ Δkm_speed`, флагнутая в доке. Multi-target supervised на трёх анти-коррелированных таргетах **математически невозможен** — composite score предсказывает 2 из 3 целей за счёт третьей.

**Ridge[km_speed_to_cart]** проваливается даже на собственный таргет (corr −0.20 при ожидаемом +0.X) — km_median слишком ortho к остальным фичам.

### B3. Синтетические инъекции

Запуск: `python3 src/metric/analysis/06_synthetic_injection.py`. Сравниваем raw-MAD per metric vs delta-MAD (Composite) по precision / recall / time-to-detect:

| Сценарий | raw P/R/TTD | delta P/R/TTD |
|---|---|---|
| `dead_search` spike +20% | 0.50 / **1.00** / **0** | 0.00 / 0.00 / 16 |
| `dead_search` step +10% | 1.00 / **1.00** / **0** | 1.00 / 0.10 / 16 |
| `dead_search` drift +1%/d×10d | 0.91 / **1.00** / **0** | 0.00 / 0.00 / 16 |
| `cart_per_click` spike −20% | **1.00** / 1.00 / 0 | 0.25 / 1.00 / 0 |
| `cart_per_click` step −10% | 1.00 / 0.29 / 10 | 1.00 / 0.10 / 16 |

### B4. Вердикт

**Delta как production-канал anomaly detection — НЕ релевантен.** Причины:

1. **Bias к target-фичам.** Composite-веса концентрированы на cart_per_click (+0.56), search_refinement (+0.28), rate_clicks (+0.23). Аномалии в quality-фичах с малым весом (dead_search_rate_inv = +0.12) почти не двигают δ_score. Видно на сценариях `dsr *` — recall ≤ 10% при raw recall 100%.
2. **Слепота к step/drift.** Delta видит только переход (изменение Δ), после которого Δ возвращается к нулю. Step-изменения проходят как один большой Δ в день инъекции и не алертятся следующие дни — recall падает до 10% против raw 100%.
3. **Высокий FP на spike.** Даже на high-weight cpc spike: delta P=0.25 vs raw P=1.00. Лишние алерты — это шум.
4. **Math: 3 anti-correlated targets ⇒ multi-target supervised не сходится.** На hold-out km_speed corr −0.37, что значит «δ_score высокий когда km_speed падает» — противоположный смысл сигнала.

### Где delta всё-таки годится (secondary)

- Hold-out corr с Δcart_rate = +0.99 и Δsession_conv = +0.99 — δ_score = **чистая структурная сводка** того, что в feature-наборе соответствует движению конверсии. Полезно как **«второе мнение»** к Health Score при движении.
- Можно держать как diagnostic в дашборде, **но не как алерт-канал**.
- В roadmap — рассмотреть EWMA/CUSUM поверх δ_score для drift, который сейчас никто не ловит.

---

## §C. Сводное Production-решение

### Production-веса

См. `configs/weights.yaml`. Within-Equal × Between-Domain. 16 фичей.

### Production-anomaly

`new_metric.ipynb §5.2`: raw-MAD per metric с порогом median ± 3·1.4826·MAD по baseline. Реализация на P1 переезжает в `src/metric/metric.py`.

### Known limitations (для презентации и follow-up)

1. **Group redundancy engagement vs discovery (0.98 corr).** Domain priors 0.35/0.25 защищают по композиции, но статистически это одна ось. Рассмотреть консолидацию.
2. **`km_median_time_to_cart_s` ortho ко всему.** Оставлен в quality как diagnostic; в Equal весах вносит свою долю (1/3), при усложнении методики (например OptMin) станет доминирующим — что нестабильно.
3. **Delta-anomaly не production.** Сохранён как research-инструмент. Документация — см. §B.
4. **NLP-фичи pending.** `complex_query_rate`, `in_product_base_rate` ждут интеграции в `build_day_summary.py`. После их появления веса будут пересчитаны (quality станет 5-фичевым, что улучшит PC1 expvar — возможно, тогда PC1 пройдёт стабильность).
5. **n=61 день мал** для bootstrap на 10 фичах. После накопления 3+ месяцев пересчитать.

### Что в коде

- `configs/weights.yaml` — единый источник production-весов
- `src/metric/analysis/02_resolve_conflicts.py` — A0
- `src/metric/analysis/03_within_group_weights.py` — A1–A3
- `src/metric/analysis/04_between_group_and_sanity.py` — A4–A5
- `src/metric/analysis/05_delta_anomaly_score.py` — B1–B2
- `src/metric/analysis/06_synthetic_injection.py` — B3–B4
- `src/metric/analysis/out/*.json` — все промежуточные числа

### Что обновить в других артефактах

- `docs/weight_calibration.md` — пометить `search_refinement_rate ↓ в quality` как deprecated (см. §A0.1)
- `src/metric/weight_calibration_consensus.ipynb` — переписать на 3-групповую структуру (текущая flat-10 несовместима с production-config)
- `src/metric/new_metric.ipynb` — `mean_duration_s` добавить в `GROUPS['engagement']`, `search_refinement_rate` перенести из `quality` в `engagement` без инверсии

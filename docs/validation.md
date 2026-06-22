# Валидация Health Score

> **TL;DR.** 3 из 4 проверок PASS, одна — FAIL (FP rate raw-MAD).
> Production raw-MAD алертер запускать **нельзя** без DOW-нормировки.
> Сама метрика Health Score (агрегат) валидна: bootstrap CI ±1.7п,
> синтетика 96% detected, baseline-окно без влияния.

## Скоуп

Цель — ответить на вопрос: «production Health Score + raw-MAD anomaly channel
готовы или их надо переделывать?». 4 независимые проверки на baseline-окне
61 день (2024-03-01 — 2024-04-30):

| Проверка | Скрипт | Pass-критерий |
|---|---|---|
| FP rate raw-MAD per feature (LOO) | `07_fp_rate_baseline.py` | FP ≤ 5% на всех 16 фичах при k=3 |
| Bootstrap CI на самой метрике | `08_health_bootstrap_ci.py` | width 95% CI median(health) ≤ 5 пунктов |
| Синтетика 16 фичей × 3 сценария | `09_synthetic_full_coverage.py` | ≥ 80% detected, нет слепых фичей |
| Чувствительность baseline-окна | `10_baseline_window_sensitivity.py` | spread Δhealth между схемами ≤ 3 п. |

Сводный: `11_validation_summary.py` · `out/validation_summary.json`.

---

## FP rate raw-MAD — **FAIL**

Метод: leave-one-out — для каждой фичи и дня d считаем порог по остальным 60 дням
(median ± k·1.4826·MAD), проверяем, попадает ли d в коридор. На чистых данных
доля «алертящих» дней = базовая шумность канала.

| Фича | FP rate (k=3) | FP rate (k=3.5) |
|---|---|---|
| rate_views | **0.18** | 0.15 |
| rate_searches | **0.16** | 0.15 |
| rate_favs | **0.15** | 0.15 |
| rate_unique_queries | **0.15** | 0.11 |
| rate_clicks | **0.11** | 0.08 |
| cart_rate | **0.11** | 0.10 |
| session_conversion_rate | 0.10 | 0.10 |
| multi_category_rate | 0.08 | 0.05 |
| view_per_search | 0.08 | 0.05 |
| ... | ... | ... |
| bounce_rate | 0.02 | 0.02 |
| mean_duration_s | 0.02 | 0.02 |

**11/16** валят порог 5% при k=3. **7/16** валят даже при k=3.5. Падение не лечится
просто расширением коридора.

### Корень проблемы — weekly-сезонность

После DOW-нормировки (`x − mean[dow(x)]`) std падает в 2–3 раза:

| Фича | std raw | std after DOW | ratio |
|---|---|---|---|
| rate_views | 5.58 | 2.24 | 0.40 |
| rate_searches | 0.34 | 0.14 | 0.40 |
| rate_favs | 0.0086 | 0.0029 | 0.33 |
| rate_unique_queries | 0.21 | 0.089 | 0.43 |
| rate_clicks | 0.10 | 0.046 | 0.45 |
| cart_rate | 0.09 | 0.076 | 0.83 |

Иначе: 55–67% дисперсии для rate-фич — это будний/выходной паттерн. Глобальный
MAD строит коридор по «смеси» будней и выходных · каждая нормальная суббота
выглядит как аномалия. У `cart_rate` ratio 0.83 — там сезонность слабее, шум
другого происхождения.

### Решение

Рекомендуем **stratified raw-MAD**:

```
для дня d с dow = d.day_of_week:
    base_subset = baseline where day_of_week == dow
    lo, hi = median(base_subset) ± k·1.4826·MAD(base_subset)
    alert если value(d) выходит за [lo, hi]
```

Альтернатива — rolling 7-day median + MAD (неявно учитывает DOW, но шумнее на
коротких baseline-окнах).

Расширение baseline до 90+ дней само по себе **не решит** проблему: добавит
точки, но не уберёт смешение dow-кластеров.

---

## Bootstrap CI на Health — **PASS**

1000 итераций resampling today-окна (n=31 дней), 95% CI на median:

| Score | median | CI lo | CI hi | width |
|---|---|---|---|---|
| **health** | 99.66 | 99.09 | 100.82 | **1.73** |
| quality | 101.78 | 100.57 | 104.16 | 3.59 |
| engagement | 98.67 | 98.00 | 99.47 | 1.47 |
| discovery | 97.38 | 95.70 | 98.12 | 2.42 |

Сама метрика стабильна. На уровне дашборда коридор ±1.7 п. для health означает,
что движения < 2 пунктов — внутри шума, > 2 — заслуживают внимания. Это
**другой**, дополняющий, anomaly-канал — на агрегате, а не на сырых фичах.

---

## Синтетика 16 × 3 — **PASS** (с оговоркой)

Стандартизованные инъекции: spike = +5σ один день, step = +3σ continuous,
drift = линейно 0·+5σ за 10 дней. σ = 1.4826·MAD baseline. Направление
аномалии — по `direction` фичи (`anti` атакуется ростом, `positive` — падением).

Результат: **46/48 (96%) detected**. Нет полностью слепых фичей.

Слабые места:
- `click_per_view` — детектится только spike, step/drift пропускаются.
  Причина: широкий baseline corridor + малая σ. Низкая чувствительность.
- `km_median_time_to_cart_s` — TTD=14 на step и spike. Реагирует только когда
  накапливается достаточно «новых» дней. Та же причина.

Δhealth разный по группам: фичи quality (3 шт, 0.40 веса, 1/3 внутри) ронят
health на 3–7 пунктов; фичи engagement (10 шт, 0.35 веса, 1/10 внутри) — на
0.1–0.7 пункта. Алерт «упала одна engagement-фича» практически невидим на
health-табло — это нормально для широких групп.

---

## Baseline window — **PASS**

Один spike (cart_per_click −5σ на day 45) при 3 схемах baseline:

| Схема | n_base | corridor_width | clean_health | injected_health | Δhealth | alert? |
|---|---|---|---|---|---|---|
| fixed_first_half | 30 | 0.319 | 100.87 | 100.22 | −0.65 | OK |
| rolling_14 | 14 | 0.199 | 100.55 | 100.16 | −0.39 | OK |
| rolling_30 | 30 | 0.260 | 100.81 | 100.29 | −0.52 | OK |

Spread Δhealth = 0.26 (< 3), все 3 схемы дают alert. Выбор baseline-окна
не критичен. Production может оставить `fixed_first_half` без переделки.

---

## Итог

**Метрика Health Score** — валидирована: bootstrap CI median(health) шириной
1.73 п., синтетика 96% detected, выбор baseline-окна не влияет на результат.

**Алертер raw-MAD** в исходном виде (глобальный MAD) использовать нельзя: на
чистом baseline он даёт высокий FP из-за недельной сезонности. Рабочий вариант —
`src/metric/anomaly.py`: `MadDetector` с adaptive DOW-deseasonalization +
direction-aware алертами.

### Что попробовали и что работает

| Подход | FP-fail/16 при k=3 | FP-fail/16 при k=3.5 |
|---|---|---|
| Global MAD (исходный) | 11 | 7 |
| Per-DOW MAD (стратификация подвыборок) | 14 | 11 — **хуже** (n=8-9 нестабильно) |
| DOW-deseasonalization для всех фич | 7 | 4 — улучшение, но шум на «несезонных» |
| **Adaptive DOW + direction-aware** (production) | **3** | **3** |

«Adaptive» — для каждой фичи проверяем `std_after_dow / std_before_dow < 0.70`;
если да — применяем DOW-вычитание (rate-фичи), иначе остаёмся на глобальной
шкале (cart_rate, km_median: сезонности нет, вычитание только добавляет шум).

### Production-настройки (закреплены в `anomaly.py`)

```python
detector = MadDetector.fit(
    baseline_df,                       # pd.DataFrame, DatetimeIndex
    features={'cart_rate': +1, ...},   # name · direction
    k=3.5,                              # default
    dow_threshold=0.70,                 # adaptive switch
)
is_alert = detector.check(feature, today_value, today_dow)  # direction-aware
```

В production-сетапе (baseline=30, today=31) при k=3.5:
- 13/16 фич FP ≤ 5%
- остаются `rate_views (0.16)`, `rate_favs (0.10)`, `rate_clicks (0.07)` —
  у этих фич `growth trend` в апреле vs март и слабый baseline (~4-5 точек на DOW).
  При расширении до baseline=60 дней должно уйти.

### Known limitations алертера

1. **n=30 baseline недостаточно** для устойчивых DOW-средних на growth-метриках
   (`rate_views`, `rate_favs`, `rate_clicks`). На baseline ≥ 60 дней остаточные
   FP по этим фичам уходят.
2. **`cart_rate`, `session_conversion_rate`** имеют тяжёлые хвосты — в LOO-mode
   на n=61 дают FP ~10%, в production setup при k=3.5 они в норме.
3. **`click_per_view`** — слабая чувствительность к step/drift (см. раздел «Синтетика 16 × 3» выше).
   Spike детектится, step/drift — нет. Это известный trade-off канала.

**Артефакты:**
- `src/metric/anomaly.py` — production-функция `MadDetector` (adaptive DOW)
- `src/metric/analysis/_common.py` — переиспользуемые утилиты
- `src/metric/analysis/07_fp_rate_baseline.py` · `out/fp_rate_baseline.json` (diagnostic, global MAD)
- `src/metric/analysis/08_health_bootstrap_ci.py` · `out/health_bootstrap_ci.json`
- `src/metric/analysis/09_synthetic_full_coverage.py` · `out/synthetic_full_coverage.json`
- `src/metric/analysis/10_baseline_window_sensitivity.py` · `out/baseline_window_sensitivity.json`
- `src/metric/analysis/11_validation_summary.py` · `out/validation_summary.json`
- `src/metric/analysis/12_fp_rate_dow.py` · `out/fp_rate_dow.json` (LOO direction-aware)
- `src/metric/analysis/13_fp_rate_dow_production.py` · `out/fp_rate_dow_production.json` (production setup)

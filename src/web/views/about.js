import { api } from "../lib/api.js";
import { h, mount } from "../lib/dom.js";
import { store } from "../lib/store.js";

function viewHead() {
  return h("header", { class: "viewhead" }, [
    h("div", {}, [
      h("div", { class: "section-num" }, "06 · About"),
      h("h1", { class: "section-title" }, "Методология и артефакты"),
      h("p", { class: "section-lede" },
        "Composite-индекс качества поиска поверх дневных агрегатов (date × category). Формула, веса, версия и пути к данным."),
    ]),
    h("div", { class: "viewhead-aside" }, [
      "Source of truth", h("br"),
      h("code", {}, "configs/weights.yaml"),
    ]),
  ]);
}

function statBlock(p) {
  if (!p) return "—";
  if (!p.exists) return h("span", { class: "is-bad" }, `missing: ${p.path}`);
  return [
    h("code", {}, p.path), " · ",
    h("span", {}, `${p.size_mb} MB`), " · ",
    h("span", { class: "is-dim" }, "updated " + (p.mtime || "").split("T")[0]),
  ];
}

export async function mountAbout(container) {
  mount(container, h("div", { class: "empty" }, "Loading metadata…"));
  let data;
  try { data = await api.about(); store.set("about", data); }
  catch (err) {
    mount(container, h("div", { class: "empty is-bad" }, "Ошибка: " + err.message));
    return;
  }

  const grid = h("div", { class: "about-grid" }, [
    h("section", {}, [
      h("h4", {}, "The metric"),
      h("p", {}, [
        "Health Score = взвешенный композит индексов 16 фичей в 3 группах. ",
        h("code", {}, "100"), " = baseline; ниже — хуже; выше — лучше.",
      ]),
      h("div", { class: "formula" },
`idx_m       = (today_m / baseline_m) · 100         (direction = +1)
            = 200 − (today_m / baseline_m) · 100   (direction = −1)

group_score = mean(idx_m for m in group)            (Equal внутри группы)
health      = Σ group_score · w_g                   (w = 0.40 / 0.35 / 0.25)`),
      h("p", {}, [
        "Равные веса внутри групп выбраны не по умолчанию, а как consensus по ",
        "результатам проверки bootstrap-стабильности (PC1 / OptMin её не прошли при n = 61 день). ",
        "Подробно — ", h("code", {}, "docs/metric_choice.md"), ".",
      ]),
    ]),

    h("section", {}, [
      h("h4", {}, "The pipeline"),
      h("p", {}, "Полный backfill из raw логов 92-дневного периода:"),
      h("div", { class: "formula" },
`python src/pipeline/run_daily_pipeline.py \\
  --in-sessions data/intent_sessions_full.parquet \\
  --baseline-start 2024-03-01 \\
  --baseline-end   2024-03-30`),
      h("p", {}, [
        "Стадии: ", h("code", {}, "raw events"), " · ",
        h("code", {}, "intent_sessions"), " · ",
        h("code", {}, "day_summary"), " · ",
        h("code", {}, "daily_metrics"), " + ",
        h("code", {}, "decomposition"), ".",
      ]),
    ]),

    h("section", {}, [
      h("h4", {}, "Artifacts on disk"),
      h("dl", {}, [
        h("dt", {}, "metrics"), h("dd", {}, statBlock(data.metrics)),
        h("dt", {}, "decomposition"), h("dd", {}, statBlock(data.decomposition)),
        h("dt", {}, "segments"), h("dd", {}, statBlock(data.segments)),
        h("dt", {}, "sessions (NLP)"), h("dd", {}, statBlock(data.sessions)),
      ]),
    ]),

    h("section", {}, [
      h("h4", {}, "Baseline window"),
      h("dl", {}, [
        h("dt", {}, "from"), h("dd", {}, data.baseline_start || "—"),
        h("dt", {}, "to"), h("dd", {}, data.baseline_end || "—"),
        h("dt", {}, "days"), h("dd", {}, String(data.n_days || 0)),
      ]),
      h("p", {}, [
        "Состав baseline зафиксирован при первом backfill — это и есть ",
        h("code", {}, "100"), " в Health Score. Сравниваем каждый день относительно него.",
      ]),
    ]),

    h("section", {}, [
      h("h4", {}, "Anomaly detector"),
      h("p", {}, [
        "MadDetector: adaptive DOW-deseasonalized raw-MAD с direction-aware ",
        "правилом (k=3.5). Если weekly-сезонность ≥30% дисперсии — применяется ",
        "вычитание DOW-средних, иначе остаёмся на глобальной шкале.",
      ]),
      h("p", {}, [
        "Состояние persisted в ", h("code", {}, "data/framework_state/mad_detector.json"),
        ", JSON-формат v1.",
      ]),
    ]),

    h("section", {}, [
      h("h4", {}, "Documentation"),
      h("dl", {}, [
        h("dt", {}, "metric"), h("dd", {}, h("code", {}, "docs/metric_choice.md")),
        h("dt", {}, "validation"), h("dd", {}, h("code", {}, "docs/validation.md")),
        h("dt", {}, "weights"), h("dd", {}, h("code", {}, "docs/weight_calibration.md")),
        h("dt", {}, "schema"), h("dd", {}, h("code", {}, "docs/schema.md")),
        h("dt", {}, "metric team"), h("dd", {}, h("code", {}, "src/metric/README.md")),
        h("dt", {}, "pipeline team"), h("dd", {}, h("code", {}, "src/pipeline/README.md")),
      ]),
    ]),
  ]);

  mount(container, h("div", {}, [
    viewHead(),
    h("section", { class: "panel" }, [
      h("div", { class: "panel-body" }, grid),
    ]),
  ]));
}

export function unmountAbout() {}

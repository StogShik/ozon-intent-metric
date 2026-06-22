import { api } from "../lib/api.js";
import { h, mount } from "../lib/dom.js";
import { fmt } from "../lib/fmt.js";
import { store } from "../lib/store.js";
import { makeHealthChart } from "../components/chart.js";

let chartInstance = null;

function viewHead() {
  return h("header", { class: "viewhead" }, [
    h("div", {}, [
      h("div", { class: "section-num" }, "01 · Overview"),
      h("h1", { class: "section-title" }, "Health Score timeline"),
      h("p", { class: "section-lede" },
        "Дневной индекс качества поиска (100 = baseline). Красные точки — дни с зафиксированной аномалией. Выберите день для детального разбора."),
    ]),
    h("div", { class: "viewhead-aside" }, [
      "16 features · 3 groups", h("br"),
      "weights 0.40 / 0.35 / 0.25",
    ]),
  ]);
}

function kpiCluster(rows) {
  if (!rows.length) return h("div");
  const avg = (k) => rows.reduce((s, r) => s + (Number(r[k]) || 0), 0) / rows.length;
  const sum = (k) => rows.reduce((s, r) => s + (Number(r[k]) || 0), 0);
  const minBy = (k) => rows.reduce((m, r) =>
    (Number(r[k]) < Number(m[k]) ? r : m), rows[0]);
  const alertDays = rows.filter((r) => r.has_alert).length;
  const worst = minBy("health_score");

  const kpi = (label, val, sub, valClass = "") =>
    h("div", { class: "kpi" }, [
      h("div", { class: "kpi-label" }, label),
      h("div", { class: `kpi-value ${valClass}` }, val),
      h("div", { class: "kpi-sub" }, sub),
    ]);

  return h("div", { class: "kpi-cluster" }, [
    kpi("Health · avg", fmt.num(avg("health_score")),
        `over ${rows.length} days`,
        fmt.healthClass(avg("health_score"))),
    kpi("Stratified · avg", fmt.num(avg("stratified_health_score")),
        `composition-neutralised`),
    kpi("Worst day", fmt.num(worst.health_score),
        worst.date, "is-bad"),
    kpi("Alert days", `${alertDays}`,
        `out of ${rows.length} · ${fmt.pct(alertDays / rows.length, 0)}`,
        alertDays > 0 ? "is-bad" : ""),
    kpi("Sessions", fmt.int(sum("n_sessions")),
        "intent sessions total"),
  ]);
}

function anomalyList(rows) {
  const alerted = rows.filter((r) => r.has_alert)
    .sort((a, b) => Number(a.health_score) - Number(b.health_score));
  if (!alerted.length) {
    return h("div", { class: "empty" }, "Аномалий не зафиксировано.");
  }
  const list = h("div", { class: "anom-list" });
  for (const r of alerted) {
    const row = h("button", {
      type: "button",
      class: "anom-row",
      onclick: () => { location.hash = `#/day/${r.date}`; },
    }, [
      h("div", { class: "anom-date" }, r.date),
      h("div", { class: "ann" }, [
        h("span", { class: fmt.healthClass(r.health_score) }, `health ${fmt.num(r.health_score)} · `),
        `${r.n_anomalies_active} fired`,
      ]),
      h("div", { class: "anom-meta" }, "разбор"),
    ]);
    list.appendChild(row);
  }
  return list;
}

export async function mountOverview(container) {
  mount(container, h("div", { class: "empty" }, "Загружаем timeline…"));

  let payload;
  try {
    payload = await api.timeline();
    store.set("timeline", payload);
  } catch (err) {
    mount(container, h("div", { class: "empty is-bad" }, "Ошибка: " + err.message));
    return;
  }

  const rows = payload.rows || [];

  const chartPanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Health timeline"),
      h("div", { class: "ph-meta" },
        `${payload.available_min} — ${payload.available_max}`),
    ]),
    h("div", { class: "chart-host" }, [
      h("canvas", { id: "ov-chart" }),
    ]),
  ]);

  const anomaliesPanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Anomaly days"),
      h("div", { class: "ph-meta" }, "click: drill"),
    ]),
    h("div", { class: "panel-body" }, anomalyList(rows)),
  ]);

  const grid = h("div", { class: "split" }, [chartPanel, anomaliesPanel]);

  const tableRows = [...rows].sort((a, b) => b.date.localeCompare(a.date));
  const table = h("table", { class: "data" }, [
    h("thead", {}, [
      h("tr", {}, [
        h("th", {}, "Date"),
        h("th", {}, "Health"),
        h("th", {}, "Strat."),
        h("th", {}, "Drift"),
        h("th", {}, "Quality"),
        h("th", {}, "Engagement"),
        h("th", {}, "Discovery"),
        h("th", {}, "Alerts"),
        h("th", {}, "Sessions"),
      ]),
    ]),
    h("tbody", {}, tableRows.map((r) =>
      h("tr", {
        dataset: { date: r.date },
        onclick: () => { location.hash = `#/day/${r.date}`; },
      }, [
        h("td", {}, [
          r.date,
          r.has_alert ? h("span", {
            class: "badge alert", style: { marginLeft: "8px" },
          }, "alert") : null,
        ]),
        h("td", { class: fmt.healthClass(r.health_score) }, fmt.num(r.health_score)),
        h("td", {}, fmt.num(r.stratified_health_score)),
        h("td", {}, fmt.delta(r.traffic_drift_signal)),
        h("td", {}, fmt.num(r.group_quality)),
        h("td", {}, fmt.num(r.group_engagement)),
        h("td", {}, fmt.num(r.group_discovery)),
        h("td", { class: r.has_alert ? "is-bad" : "is-dim" }, String(r.n_anomalies_active || 0)),
        h("td", {}, fmt.int(r.n_sessions)),
      ]))),
  ]);

  const tablePanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Daily table"),
      h("div", { class: "ph-meta" }, `${rows.length} rows · click: drill`),
    ]),
    h("div", { style: { maxHeight: "520px", overflow: "auto" } }, table),
  ]);

  mount(container, h("div", {}, [
    viewHead(),
    kpiCluster(rows),
    grid,
    tablePanel,
  ]));

  if (chartInstance) chartInstance.destroy();
  const canvas = document.getElementById("ov-chart");
  if (canvas && rows.length) {
    chartInstance = makeHealthChart(canvas, rows, {
      onPick: (date) => { location.hash = `#/day/${date}`; },
    });
  }
}

export function unmountOverview() {
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
}

import { api } from "../lib/api.js";
import { h, mount } from "../lib/dom.js";
import { fmt } from "../lib/fmt.js";
import { store } from "../lib/store.js";
import { segmentLabel } from "../lib/labels.js";

function viewHead(state, normSrc) {
  const dayMode = Boolean(state.date);
  const normWord = normSrc === "baseline" ? "baseline-период" : "весь период";
  return h("header", { class: "viewhead" }, [
    h("div", {}, [
      h("div", { class: "section-num" },
        dayMode ? `04 · Weak spots · ${state.date}` : "04 · Weak spots"),
      h("h1", { class: "section-title" },
        dayMode ? "Просадка по сегментам за день" : "Системные слабые места"),
      h("p", { class: "section-lede" },
        dayMode
          ? `Сегменты сравниваются со своей нормой за ${normWord}. Extra failures — число сессий, не дошедших до корзины сверх ожидаемого по норме сегмента.`
          : "Доля успешных сессий (to_cart) по сегментам трафика. Ранжирование по объёму потерь: n_sessions × (1 − success_rate)."),
    ]),
    h("div", { class: "viewhead-aside" }, [
      "8 segments · NLP + behavior", h("br"),
      dayMode
        ? h("a", { href: "#/weak-spots" }, "весь период")
        : "click row: real examples",
    ]),
  ]);
}

function controls(state, onSubmit) {
  const tl = store.get("timeline");
  const dateAttrs = { id: "ws-date", class: "input", type: "date",
                      value: state.date, style: { width: "150px" } };
  if (tl && tl.available_min) dateAttrs.min = tl.available_min;
  if (tl && tl.available_max) dateAttrs.max = tl.available_max;

  return h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Filters"),
      h("div", { class: "ph-meta" },
        state.date ? "day mode · ranked by extra failures" : "tune the ranking"),
    ]),
    h("form", {
      class: "panel-body",
      onsubmit: (e) => { e.preventDefault(); onSubmit(); },
    }, [
      h("div", { class: "field-grp" }, [
        h("div", { class: "field" }, [
          h("label", {}, "day (empty = period)"),
          h("input", dateAttrs),
        ]),
        h("div", { class: "field" }, [
          h("label", {}, "min volume"),
          h("input", { id: "ws-min-volume", class: "input", type: "number",
                       value: state.minVolume, min: 0, step: 100, style: { width: "110px" } }),
        ]),
        h("div", { class: "field" }, [
          h("label", {}, "max lift (worse on)"),
          h("input", { id: "ws-min-lift", class: "input", type: "number",
                       value: state.minLift, step: 0.01, style: { width: "100px" },
                       disabled: Boolean(state.date),
                       title: state.date ? "не используется в day mode" : "" }),
        ]),
        h("div", { class: "field" }, [
          h("label", {}, "top N"),
          h("input", { id: "ws-top", class: "input", type: "number",
                       value: state.top, min: 1, max: 100, style: { width: "70px" } }),
        ]),
        h("button", { class: "btn", type: "submit" }, "Apply"),
      ]),
    ]),
  ]);
}

function groupBySegment(rows, sortKey) {
  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.segment)) groups.set(r.segment, []);
    groups.get(r.segment).push(r);
  }
  return Array.from(groups.entries())
    .map(([seg, rs]) => ({
      seg, rs,
      total: rs.reduce((s, r) => s + (Number(r[sortKey]) || 0), 0),
    }))
    .sort((a, b) => b.total - a.total);
}

function periodTable(rows) {
  if (!rows.length) {
    return h("div", { class: "empty" }, "No weak spots above this threshold.");
  }
  const tbody = h("tbody", {});
  for (const { seg, rs, total } of groupBySegment(rows, "failure_volume")) {
    tbody.appendChild(h("tr", { class: "tg-head" }, [
      h("td", { colspan: 7 }, [
        h("span", { class: "tg-name" }, segmentLabel(seg)),
        h("span", { class: "tg-meta" },
          `${rs.length} bucket${rs.length > 1 ? "s" : ""} · ` +
          `${fmt.int(total)} failed sessions in segment`),
      ]),
    ]));
    for (const r of rs) {
      const liftCls = Number(r.lift_vs_overall) < -0.10 ? "is-bad" :
                      Number(r.lift_vs_overall) < -0.05 ? "is-warn" : "";
      tbody.appendChild(h("tr", {
        onclick: () => {
          location.hash = `#/examples?segment=${encodeURIComponent(r.segment)}` +
                          `&value=${encodeURIComponent(r.value)}`;
        },
      }, [
        h("td", { class: "tg-row-val" }, String(r.value)),
        h("td", {}, fmt.int(r.n_sessions)),
        h("td", {}, fmt.pct(r.success_rate)),
        h("td", { class: liftCls }, fmt.delta(Number(r.lift_vs_overall) * 100, 1) + " pp"),
        h("td", { class: "is-bad" }, fmt.int(r.failure_volume)),
        h("td", {}, fmt.num(r.median_n_queries, 1)),
        h("td", {}, fmt.num(r.median_n_views, 1)),
      ]));
    }
  }
  const table = h("table", { class: "data weak-table" }, [
    h("thead", {}, [
      h("tr", {}, [
        h("th", {}, "Value"),
        h("th", {}, "Sessions"),
        h("th", {}, "Success"),
        h("th", {}, "Lift vs overall"),
        h("th", {}, "Failure volume"),
        h("th", {}, "Median queries"),
        h("th", {}, "Median views"),
      ]),
    ]),
    tbody,
  ]);
  return h("div", { style: { maxHeight: "600px", overflow: "auto" } }, table);
}

function dayTable(rows, date, normSrc) {
  const normCol = normSrc === "baseline" ? "baseline" : "period";
  if (!rows.length) {
    return h("div", { class: "empty" },
      "Нет per-day данных по сегментам. Пересоберите segment_breakdown через " +
      "run_daily_pipeline.py --segments-sessions …");
  }
  const tbody = h("tbody", {});
  for (const { seg, rs, total } of groupBySegment(rows, "extra_failures")) {
    tbody.appendChild(h("tr", { class: "tg-head" }, [
      h("td", { colspan: 7 }, [
        h("span", { class: "tg-name" }, segmentLabel(seg)),
        h("span", { class: "tg-meta" },
          `${rs.length} bucket${rs.length > 1 ? "s" : ""} · ` +
          `${fmt.delta(total, 0)} extra failures vs norm`),
      ]),
    ]));
    for (const r of rs) {
      const dpp = Number(r.delta_vs_period) * 100;
      const dCls = dpp < -1 ? "is-bad" : dpp > 1 ? "is-good" : "is-dim";
      const extra = Number(r.extra_failures) || 0;
      tbody.appendChild(h("tr", {
        onclick: () => {
          location.hash = `#/examples?segment=${encodeURIComponent(r.segment)}` +
                          `&value=${encodeURIComponent(r.value)}` +
                          `&date=${encodeURIComponent(date)}`;
        },
      }, [
        h("td", { class: "tg-row-val" }, String(r.value)),
        h("td", {}, fmt.int(r.n_sessions)),
        h("td", {}, fmt.pct(r.success_rate)),
        h("td", { class: "is-dim" }, fmt.pct(r.success_rate_period)),
        h("td", { class: dCls }, fmt.delta(dpp, 1) + " pp"),
        h("td", { class: extra > 0 ? "is-bad" : "is-good" }, fmt.delta(extra, 0)),
        h("td", {}, fmt.num(r.median_n_queries, 1)),
      ]));
    }
  }
  const table = h("table", { class: "data weak-table" }, [
    h("thead", {}, [
      h("tr", {}, [
        h("th", {}, "Value"),
        h("th", {}, "Sessions · day"),
        h("th", {}, "Success · day"),
        h("th", {}, `Success · ${normCol}`),
        h("th", {}, `Δ vs ${normCol}`),
        h("th", {}, "Extra failures"),
        h("th", {}, "Median queries"),
      ]),
    ]),
    tbody,
  ]);
  return h("div", { style: { maxHeight: "600px", overflow: "auto" } }, table);
}

export async function mountWeakSpots(container, params) {
  const state = {
    minVolume: params.min_volume || 200,
    minLift: params.min_lift || -0.05,
    top: params.top || 20,
    date: params.date || "",
  };

  mount(container, h("div", { class: "empty" }, "Loading weak spots…"));

  const ctrl = controls(state, async () => {
    state.minVolume = +document.getElementById("ws-min-volume").value;
    state.minLift = +document.getElementById("ws-min-lift").value;
    state.top = +document.getElementById("ws-top").value;
    state.date = document.getElementById("ws-date").value || "";
    const dateQ = state.date ? `&date=${state.date}` : "";
    location.hash =
      `#/weak-spots?min_volume=${state.minVolume}&min_lift=${state.minLift}` +
      `&top=${state.top}${dateQ}`;
  });

  let weak;
  try {
    weak = await api.weakSpots({
      min_volume: state.minVolume,
      min_lift: state.date ? "" : state.minLift,
      top: state.top,
      date: state.date,
    });
  } catch (err) {
    mount(container, h("div", { class: "empty is-bad" }, "Ошибка: " + err.message));
    return;
  }

  const rows = weak.rows || [];
  const normSrc = (weak.norm && weak.norm.source) || "period";
  const tablePanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, state.date ? `Segments on ${state.date}` : "Top weak spots"),
      h("div", { class: "ph-meta" },
        `${rows.length} rows · click: real examples`),
    ]),
    state.date ? dayTable(rows, state.date, normSrc) : periodTable(rows),
  ]);

  mount(container, h("div", {}, [
    viewHead(state, normSrc),
    ctrl,
    tablePanel,
  ]));
}

export function unmountWeakSpots() {}

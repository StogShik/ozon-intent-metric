import { api } from "../lib/api.js";
import { h, mount } from "../lib/dom.js";
import { fmt } from "../lib/fmt.js";

function viewHead(from, to) {
  return h("header", { class: "viewhead" }, [
    h("div", {}, [
      h("div", { class: "section-num" }, `03 · Δ explain · ${from} — ${to}`),
      h("h1", { class: "section-title" }, "Разбор изменения за два дня"),
      h("p", { class: "section-lede" },
        "Δcontribution = вклад в Health изменился между двумя датами. Сумма по фичам ≈ ΔHealth (raw); по категориям ≈ ΔStratified."),
    ]),
    h("div", { class: "viewhead-aside" }, [
      h("a", { href: `#/day/${from}` }, `от ${from}`),
      h("br"),
      h("a", { href: `#/day/${to}` }, `к ${to}`),
    ]),
  ]);
}

function deltaSummary(data) {
  const dRaw = Number(data.delta_health_raw);
  const dStrat = Number(data.delta_health_stratified);
  return h("div", { class: "delta-summary" }, [
    h("div", { class: "ds" }, [
      h("div", { class: "ds-label" }, "Δ Health · raw"),
      h("div", { class: `ds-value ${dRaw < 0 ? "is-bad" : "is-good"}` }, fmt.delta(dRaw)),
    ]),
    h("div", { class: "ds" }, [
      h("div", { class: "ds-label" }, "Δ Health · stratified"),
      h("div", { class: `ds-value ${dStrat < 0 ? "is-bad" : "is-good"}` }, fmt.delta(dStrat)),
    ]),
  ]);
}

function deltaList(items, nameKey) {
  const list = h("div", { class: "delta-list" });
  if (!items || items.length === 0) {
    list.appendChild(h("div", { class: "empty" }, "—"));
    return list;
  }
  for (const r of items) {
    const d = Number(r.delta_contribution) || 0;
    const cls = d < 0 ? "neg" : d > 0 ? "pos" : "";
    const name = r[nameKey] || r.feature || r.group || r.category || "—";
    const row = h("div", { class: `delta-row ${cls}` }, [
      h("div", { class: "d-name" }, name),
      h("div", { class: `d-delta ${d < 0 ? "is-bad" : "is-good"}` }, fmt.delta(d, 2)),
      h("div", { class: "d-trail" }, `${fmt.num(r.from_index)} · ${fmt.num(r.to_index)}`),
    ]);
    list.appendChild(row);
  }
  return list;
}

export async function mountCompare(container, params) {
  const { from, to } = params;
  if (!from || !to) {
    mount(container, h("div", { class: "empty" }, "Compare needs two dates: #/compare/D1..D2"));
    return;
  }
  mount(container, h("div", { class: "empty" }, `Computing Δ ${from} — ${to}…`));
  let data;
  try { data = await api.explain(from, to, 12); }
  catch (err) {
    mount(container, h("div", { class: "empty is-bad" }, "Ошибка: " + err.message));
    return;
  }

  const groupsPanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "By group"),
      h("div", { class: "ph-meta" }, "3 · domain weights"),
    ]),
    h("div", { class: "panel-body" }, deltaList(data.by_group, "group")),
  ]);
  const featuresPanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Top feature contributors"),
      h("div", { class: "ph-meta" }, "worst Δ first"),
    ]),
    h("div", { class: "panel-body" }, deltaList(data.by_feature_top, "feature")),
  ]);
  const catsPanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Top category contributors"),
      h("div", { class: "ph-meta" }, "worst Δ first"),
    ]),
    h("div", { class: "panel-body" }, deltaList(data.by_category_top, "category")),
  ]);

  const caveats = (data.caveats || []).length
    ? h("section", { class: "panel" }, [
        h("div", { class: "panel-head" }, [
          h("h3", { class: "is-warn" }, "Caveats"),
        ]),
        h("div", { class: "panel-body" },
          (data.caveats || []).map((c) =>
            h("div", { class: "is-warn", style: { marginBottom: "6px" } }, c))),
      ])
    : null;

  mount(container, h("div", {}, [
    viewHead(from, to),
    deltaSummary(data),
    h("div", { class: "split-3" }, [groupsPanel, featuresPanel, catsPanel]),
    caveats,
  ]));
}

export function unmountCompare() {}

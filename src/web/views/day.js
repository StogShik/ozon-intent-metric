import { api } from "../lib/api.js";
import { h, mount } from "../lib/dom.js";
import { fmt } from "../lib/fmt.js";
import { store } from "../lib/store.js";
import { barList } from "../components/bars.js";
import { segmentLabel } from "../lib/labels.js";

function viewHead(date, dayRow) {
  return h("header", { class: "viewhead" }, [
    h("div", {}, [
      h("div", { class: "section-num" }, `02 · Day detail · ${date}`),
      h("h1", { class: "section-title" }, "Декомпозиция за день"),
      h("p", { class: "section-lede" },
        "Аддитивный разбор: сумма contribution по фичам = Health (raw); сумма по категориям (included) = Stratified."),
    ]),
    h("div", { class: "viewhead-aside" }, [
      "Compare with", h("br"),
      dayRow ? `prev: ${store.get("prevSelectedDate") || "—"}` : "—", h("br"),
      store.canCompare()
        ? h("a", { href: `#/compare/${store.get("prevSelectedDate")}..${date}` },
            `Δ explain`)
        : "(select another day)",
      h("br"),
      h("a", { href: `#/weak-spots?date=${date}` }, "weak spots · this day"),
    ]),
  ]);
}

function summaryKPIs(canonical, dayRow) {
  const kpi = (label, value, sub, valClass = "") =>
    h("div", { class: "kpi" }, [
      h("div", { class: "kpi-label" }, label),
      h("div", { class: `kpi-value ${valClass}` }, value),
      h("div", { class: "kpi-sub" }, sub),
    ]);
  return h("div", { class: "kpi-cluster" }, [
    kpi("Health", fmt.num(canonical),
        "100 = baseline", fmt.healthClass(canonical)),
    kpi("Stratified", fmt.num(dayRow && dayRow.stratified_health_score),
        "fixed baseline mix"),
    kpi("Drift", fmt.delta(dayRow && dayRow.traffic_drift_signal),
        "stratified − raw"),
    kpi("Anomalies", String((dayRow && dayRow.n_anomalies_active) || 0),
        dayRow && dayRow.has_alert ? "MAD fired" : "all in corridor",
        dayRow && dayRow.has_alert ? "is-bad" : "is-good"),
    kpi("Sessions", fmt.int(dayRow && dayRow.n_sessions),
        "intent sessions"),
  ]);
}

function corridorLine(f, alert) {
  if (f.corridor_lo == null || f.corridor_hi == null) return null;
  const sigma = Number(f.sigma);
  const resid = Number(f.residual);
  const sigmas = sigma > 0 && !Number.isNaN(resid) ? resid / sigma : null;
  return h("div", { class: "f-corr" }, [
    `corridor ${fmt.num(f.corridor_lo, 3)}…${fmt.num(f.corridor_hi, 3)}`,
    sigmas != null && !Number.isNaN(sigmas)
      ? h("span", { class: alert ? "is-bad" : "is-dim" }, ` · ${fmt.delta(sigmas, 1)}σ`)
      : null,
  ]);
}

function featureList(features) {
  const sorted = [...features]
    .filter((f) => f.contribution != null && !Number.isNaN(Number(f.contribution)))
    .sort((a, b) => Number(a.contribution) - Number(b.contribution));
  const list = h("div", { class: "feature-list" });
  for (const f of sorted) {
    const alert = f.is_anomaly === true || f.is_anomaly === "True";
    const row = h("div", { class: `feature-row ${alert ? "alert" : ""}` }, [
      h("div", {}, [
        h("div", { class: "f-name" }, [
          f.feature,
          alert ? h("span", { class: "badge alert", style: { marginLeft: "8px" } }, "alert") : null,
        ]),
        h("div", { class: "f-grp" }, [
          f.group || "",
          f.dow_used === true || f.dow_used === "True" ? " · DOW" : "",
        ]),
        corridorLine(f, alert),
      ]),
      h("div", { class: `f-idx ${fmt.healthClass(f.index_value)}` }, fmt.num(f.index_value)),
      h("div", { class: "f-contrib" }, fmt.delta(f.contribution)),
      h("div", { class: "f-today" },
        f.today_value != null ? fmt.num(f.today_value, 3) : "—"),
    ]);
    list.appendChild(row);
  }
  return list;
}

function weakSegmentsPanel(date, segResp) {
  if (!segResp || !Array.isArray(segResp.rows) || segResp.rows.length === 0) return null;
  const rows = segResp.rows;
  const normCol = segResp.norm && segResp.norm.source === "baseline" ? "baseline" : "period";
  const tbody = h("tbody", {}, rows.map((r) => {
    const dpp = Number(r.delta_vs_period) * 100;
    const dCls = dpp < -1 ? "is-bad" : dpp > 1 ? "is-good" : "is-dim";
    const extra = Number(r.extra_failures) || 0;
    return h("tr", {
      onclick: () => {
        location.hash = `#/examples?segment=${encodeURIComponent(r.segment)}` +
                        `&value=${encodeURIComponent(r.value)}` +
                        `&date=${encodeURIComponent(date)}`;
      },
    }, [
      h("td", {}, segmentLabel(r.segment)),
      h("td", { class: "tg-row-val" }, String(r.value)),
      h("td", {}, fmt.int(r.n_sessions)),
      h("td", {}, fmt.pct(r.success_rate)),
      h("td", { class: "is-dim" }, fmt.pct(r.success_rate_period)),
      h("td", { class: dCls }, fmt.delta(dpp, 1) + " pp"),
      h("td", { class: extra > 0 ? "is-bad" : "is-good" }, fmt.delta(extra, 0)),
    ]);
  }));
  const table = h("table", { class: "data" }, [
    h("thead", {}, [
      h("tr", {}, [
        h("th", {}, "Segment"),
        h("th", {}, "Value"),
        h("th", {}, "Sessions"),
        h("th", {}, "Success · day"),
        h("th", {}, `Success · ${normCol}`),
        h("th", {}, `Δ vs ${normCol}`),
        h("th", {}, "Extra failures"),
      ]),
    ]),
    tbody,
  ]);
  return h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Weak segments · this day"),
      h("div", { class: "ph-meta" }, [
        `top extra failures vs ${normCol} norm · `,
        h("a", { href: `#/weak-spots?date=${date}` }, "all segments"),
      ]),
    ]),
    table,
  ]);
}

function categoryList(cats) {
  const items = cats
    .filter((c) => c.included && c.contribution != null)
    .sort((a, b) => Number(a.contribution) - Number(b.contribution))
    .slice(0, 14)
    .map((c) => ({
      label: c.category,
      index_value: c.index_value,
      contribution: c.contribution,
      n_sessions: c.n_sessions_today,
    }));
  return barList(items, { showContrib: true });
}

export async function mountDay(container, params) {
  const date = params.date;
  if (!date) {
    mount(container, h("div", { class: "empty" }, "Date is required. Try #/overview to pick one."));
    return;
  }
  store.selectDay(date);

  mount(container, h("div", { class: "empty" }, `Loading ${date}…`));

  let data, dayRow, segResp;
  try {
    [data, segResp] = await Promise.all([
      api.day(date),
      api.weakSpots({ date, top: 6, min_volume: 200 }).catch(() => null),
    ]);
    const tl = store.get("timeline");
    dayRow = tl && tl.rows.find((r) => r.date === date);
  } catch (err) {
    mount(container, h("div", { class: "empty is-bad" }, "Ошибка: " + err.message));
    return;
  }

  const canonicalHealth = dayRow ? Number(dayRow.health_score) : NaN;

  const featSum = data.features
    .map((f) => Number(f.contribution))
    .filter((v) => !Number.isNaN(v))
    .reduce((a, b) => a + b, 0);
  if (!Number.isNaN(canonicalHealth) && Math.abs(canonicalHealth - featSum) > 0.01) {
    console.warn(`Day drill: canonical=${canonicalHealth} vs Σcontrib=${featSum}`);
  }

  const groupsPanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Groups"),
      h("div", { class: "ph-meta" }, "3 · domain weights"),
    ]),
    h("div", { class: "panel-body" }, barList(
      (data.groups || []).map((g) => ({
        label: g.group, index_value: g.index_value, contribution: g.contribution,
      })),
      { showContrib: true },
    )),
  ]);

  const featuresPanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Features"),
      h("div", { class: "ph-meta" }, `${data.features.length} · sorted worst to best`),
    ]),
    h("div", { class: "panel-body" }, featureList(data.features)),
  ]);

  const catsPanel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, "Top weak categories"),
      h("div", { class: "ph-meta" }, "stratified · top 14"),
    ]),
    h("div", { class: "panel-body" }, categoryList(data.categories || [])),
  ]);

  const grid = h("div", { class: "split-3" }, [groupsPanel, featuresPanel, catsPanel]);

  mount(container, h("div", {}, [
    viewHead(date, dayRow),
    summaryKPIs(canonicalHealth, dayRow),
    grid,
    weakSegmentsPanel(date, segResp),
  ]));
}

export function unmountDay() {}

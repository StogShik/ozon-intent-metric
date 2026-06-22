import { h, clear } from "./lib/dom.js";
import { api } from "./lib/api.js";
import { store } from "./lib/store.js";

import { mountOverview, unmountOverview } from "./views/overview.js";
import { mountDay, unmountDay } from "./views/day.js";
import { mountCompare, unmountCompare } from "./views/compare.js";
import { mountWeakSpots, unmountWeakSpots } from "./views/weak-spots.js";
import { mountExamples, unmountExamples } from "./views/examples.js";
import { mountAbout, unmountAbout } from "./views/about.js";

const NAV = [
  { num: "01", id: "overview", label: "Overview",   route: "#/overview",   sub: "timeline + KPIs" },
  { num: "02", id: "day",      label: "Day detail", route: "#/day",        sub: "per-day RCA" },
  { num: "03", id: "compare",  label: "Δ explain",  route: "#/compare",    sub: "two days diff" },
  { num: "04", id: "weak",     label: "Weak spots", route: "#/weak-spots", sub: "segment failures" },
  { num: "05", id: "examples", label: "Examples",   route: "#/examples",   sub: "session samples" },
  { num: "06", id: "about",    label: "About",      route: "#/about",      sub: "method, artifacts" },
];

const VIEWS = {
  overview: { mount: mountOverview, unmount: unmountOverview },
  day:      { mount: mountDay, unmount: unmountDay },
  compare:  { mount: mountCompare, unmount: unmountCompare },
  weak:     { mount: mountWeakSpots, unmount: unmountWeakSpots },
  examples: { mount: mountExamples, unmount: unmountExamples },
  about:    { mount: mountAbout, unmount: unmountAbout },
};

const VIEW_BY_KEY = {
  overview:    "overview",
  day:         "day",
  compare:     "compare",
  "weak-spots":"weak",
  examples:    "examples",
  about:       "about",
};

let currentView = null;
const viewEl = document.getElementById("view");


function renderNav(activeId) {
  const nav = document.getElementById("rail-nav");
  clear(nav);
  for (const item of NAV) {
    nav.appendChild(h("button", {
      class: `nav-item ${item.id === activeId ? "is-active" : ""}`,
      type: "button",
      onclick: () => { location.hash = item.route; },
    }, [
      h("span", { class: "nav-num" }, item.num),
      h("div", {}, [
        h("div", {}, item.label),
        h("div", { class: "nav-sub" }, item.sub),
      ]),
    ]));
  }
}


function parseHash() {
  let h = (location.hash || "").replace(/^#\/?/, "");
  if (!h) return { key: "overview", params: {} };

  const [path, queryStr] = h.split("?");
  const segs = path.split("/").filter(Boolean);
  const key = segs[0] || "overview";
  const tail = segs.slice(1);

  const query = {};
  if (queryStr) {
    for (const [k, v] of new URLSearchParams(queryStr).entries()) query[k] = v;
  }

  switch (key) {
    case "day":
      query.date = tail[0] || query.date;
      break;
    case "compare": {
      if (tail[0] && tail[0].includes("..")) {
        const [from, to] = tail[0].split("..");
        query.from = from; query.to = to;
      }
      break;
    }
    case "weak-spots":
      break;
    case "examples":
      if (tail.length >= 2) {
        query.segment = decodeURIComponent(tail[0]);
        query.value = decodeURIComponent(tail[1]);
      }
      break;
  }
  return { key, params: query };
}


async function route() {
  const { key, params } = parseHash();
  const viewKey = VIEW_BY_KEY[key] || "overview";

  if (currentView && VIEWS[currentView]?.unmount) {
    VIEWS[currentView].unmount();
  }
  currentView = viewKey;

  const navId = NAV.find((n) => n.id === viewKey)?.id || "overview";
  renderNav(navId);

  if (key === "day" && !params.date) { location.hash = "#/overview"; return; }
  if (key === "compare" && (!params.from || !params.to)) {
    if (store.canCompare()) {
      const from = store.get("prevSelectedDate");
      const to = store.get("selectedDate");
      location.hash = `#/compare/${from}..${to}`;
      return;
    }
    location.hash = "#/overview";
    return;
  }

  document.querySelector(".main")?.scrollTo({ top: 0, behavior: "instant" });

  try {
    await VIEWS[viewKey].mount(viewEl, params);
  } catch (err) {
    clear(viewEl);
    viewEl.appendChild(h("div", { class: "empty is-bad" },
      `View error: ${err.message}`));
  }
}


async function bootstrapMeta() {
  const statusText = document.getElementById("brand-status-text");
  const rfBaseline = document.getElementById("rf-baseline");
  const rfDays = document.getElementById("rf-days");
  const rfAnom = document.getElementById("rf-anom");
  try {
    const [about, timeline] = await Promise.all([api.about(), api.timeline()]);
    store.set("about", about);
    store.set("timeline", timeline);
    const alertDays = (timeline.rows || []).filter((r) => r.has_alert).length;
    statusText.textContent =
      `${timeline.rows.length} days · ${alertDays} anomaly`;
    rfBaseline.textContent =
      `${about.baseline_start || "—"} … ${about.baseline_end || "—"}`;
    rfDays.textContent = String(about.n_days || "—");
    rfAnom.textContent = `${alertDays} of ${timeline.rows.length}`;
  } catch (err) {
    statusText.textContent = "offline";
    statusText.classList.add("is-bad");
  }
}


window.addEventListener("hashchange", route);
document.addEventListener("DOMContentLoaded", async () => {
  await bootstrapMeta();
  if (!location.hash) location.hash = "#/overview";
  else route();
});

if (document.readyState === "complete" || document.readyState === "interactive") {
  bootstrapMeta().then(() => {
    if (!location.hash) location.hash = "#/overview";
    else route();
  });
}

import { h } from "../lib/dom.js";
import { fmt } from "../lib/fmt.js";

const RANGE = { min: 50, max: 150 };

function pct(v, min = RANGE.min, max = RANGE.max) {
  return Math.max(0, Math.min(1, (v - min) / (max - min)));
}

export function barList(items, opts = {}) {
  const valueKey = opts.valueKey || "index_value";
  const labelKey = opts.labelKey || "label";
  const showContrib = opts.showContrib || false;

  const container = h("div", { class: "bars" });

  if (!items || items.length === 0) {
    container.appendChild(h("div", { class: "empty" }, "no data"));
    return container;
  }

  for (const it of items) {
    const raw = Number(it[valueKey]);
    if (Number.isNaN(raw)) continue;
    const clipped = Math.max(RANGE.min, Math.min(RANGE.max, raw));
    const fillPct = pct(clipped);
    const midPct = pct(100);
    const cls = raw < 95 ? "below" : raw > 105 ? "above" : "";
    const overflow = raw !== clipped;

    const bar = h("div", { class: "bar" }, [
      h("div", { class: "bar-meta" }, [
        h("span", { class: "bar-label" }, it[labelKey] || "—"),
        h("span", { class: "bar-value" }, [
          h("span", { class: fmt.healthClass(raw) }, fmt.num(raw)),
          showContrib
            ? h("small", {}, `contrib ${fmt.delta(it.contribution, 2)}`)
            : null,
        ]),
      ]),
      h("div", { class: "bar-track" }, [
        h("div", { class: `bar-fill ${cls}`, style: { left: "0", width: `${fillPct * 100}%` } }),
        h("div", { class: "bar-mid", style: { left: `${midPct * 100}%` } }),
      ]),
      overflow
        ? h("div", {
            class: "is-dim",
            style: { fontSize: "10px", fontFamily: "var(--font-mono)", marginTop: "2px" },
          }, `clipped (raw ${fmt.num(raw)})`)
        : null,
    ]);

    container.appendChild(bar);
  }

  return container;
}

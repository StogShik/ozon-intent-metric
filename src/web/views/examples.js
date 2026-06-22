import { api } from "../lib/api.js";
import { h, mount } from "../lib/dom.js";
import { fmt } from "../lib/fmt.js";
import { segmentLabel } from "../lib/labels.js";

function viewHead(segment, value, date) {
  return h("header", { class: "viewhead" }, [
    h("div", {}, [
      h("div", { class: "section-num" },
        `05 · Examples · ${segment} = ${value}` + (date ? ` · ${date}` : "")),
      h("h1", { class: "section-title" }, "Сессии без конверсии"),
      h("p", { class: "section-lede" },
        "Сессии без to_cart (reached_cart = False): запрос, число уточнений, виджет входа и NLP-индикаторы."),
    ]),
    h("div", { class: "viewhead-aside" }, [
      "Segment", h("br"),
      h("strong", {}, segmentLabel(segment)), h("br"),
      "Value: " + value,
      date ? h("span", {}, [h("br"), "Day: " + date]) : null,
    ]),
  ]);
}

function exampleCard(e) {
  const q = e.first_query || e.query_clean || "—";
  const meta = [];
  const push = (label, val) => {
    if (val == null || val === "" || val === false) return;
    meta.push(h("span", {}, [label, " ", h("strong", {}, String(val))]));
  };
  push("queries", e.n_unique_queries);
  push("views", e.n_view);
  push("clicks", e.n_click);
  push("time", fmt.duration(e.duration_s));
  push("category", e.top_category_in_session);
  push("widget", e.first_widget);
  push("nlp", e.query_stratification_score);
  push("words", e.query_len_words);
  push("base?", e.is_in_product_base === true ? "yes" : e.is_in_product_base === false ? "no" : null);

  return h("div", { class: "example" }, [
    h("q", {}, q),
    h("div", { class: "ex-meta" }, meta),
  ]);
}

export async function mountExamples(container, params) {
  const segment = params.segment;
  const value = params.value;
  const date = params.date || null;
  if (!segment || !value) {
    mount(container, h("div", { class: "empty" },
      "Open from a Weak spots row, or pass ?segment=…&value=…"));
    return;
  }
  mount(container, h("div", { class: "empty" }, `Loading examples for ${segment}=${value}…`));

  let data;
  try { data = await api.examples(segment, value, params.n || 20, date); }
  catch (err) {
    mount(container, h("div", { class: "empty is-bad" }, "Ошибка: " + err.message));
    return;
  }

  const examples = data.examples || [];
  const panel = h("section", { class: "panel" }, [
    h("div", { class: "panel-head" }, [
      h("h3", {}, `Examples`),
      h("div", { class: "ph-meta" },
        `${examples.length} · failed sessions` +
        (date ? ` · ${date}` : "") + ` · назад через меню слева`),
    ]),
    h("div", { class: "panel-body" }, [
      examples.length === 0
        ? h("div", { class: "empty" }, "Примеры не найдены (возможно, артефакт сессий недоступен).")
        : h("div", { class: "examples" }, examples.map(exampleCard)),
    ]),
  ]);

  mount(container, h("div", {}, [
    viewHead(segment, value, date),
    panel,
  ]));
}

export function unmountExamples() {}

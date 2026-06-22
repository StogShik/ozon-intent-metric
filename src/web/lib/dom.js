export function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset" && typeof v === "object") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === "html") {
      el.innerHTML = v;
    } else if (v === false || v == null) {
    } else if (v === true) {
      el.setAttribute(k, "");
    } else {
      el.setAttribute(k, v);
    }
  }
  if (!Array.isArray(children)) children = [children];
  for (const c of children) {
    if (c == null || c === false) continue;
    if (typeof c === "string" || typeof c === "number") {
      el.appendChild(document.createTextNode(String(c)));
    } else if (c instanceof Node) {
      el.appendChild(c);
    } else if (Array.isArray(c)) {
      for (const cc of c) {
        if (cc != null && cc !== false) {
          el.appendChild(typeof cc === "string" || typeof cc === "number"
            ? document.createTextNode(String(cc)) : cc);
        }
      }
    }
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

export function mount(container, node) {
  clear(container);
  if (node) container.appendChild(node);
}

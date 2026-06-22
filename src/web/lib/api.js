async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    let body = {};
    try { body = await res.json(); } catch (_) {}
    throw new Error(body.error || body.message || res.statusText || `HTTP ${res.status}`);
  }
  return res.json();
}

function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v == null || v === "") continue;
    u.set(k, v);
  }
  const s = u.toString();
  return s ? "?" + s : "";
}

export const api = {
  timeline: (opts = {}) => fetchJSON("/api/timeline" + qs(opts)),
  day:      (date)      => fetchJSON("/api/day" + qs({ date })),
  explain:  (from, to, top_n = 10) => fetchJSON("/api/explain" + qs({ from, to, top_n })),
  weakSpots:(opts = {}) => fetchJSON("/api/weak-spots" + qs(opts)),
  examples: (segment, value, n = 10, date = null) =>
    fetchJSON("/api/examples" + qs({ segment, value, n, date })),
  about:    ()          => fetchJSON("/api/about"),
};

const startInput = document.querySelector("#start");
const endInput = document.querySelector("#end");
const form = document.querySelector("#filters");
const rowsEl = document.querySelector("#rows");
const sourceEl = document.querySelector("#source");
const rangeNoteEl = document.querySelector("#range-note");

const kpiHealth = document.querySelector("#kpi-health");
const kpiSearches = document.querySelector("#kpi-searches");
const kpiSessions = document.querySelector("#kpi-sessions");
const kpiConverted = document.querySelector("#kpi-converted");

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("ru-RU", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function fmtInt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 0 });
}

function fmtDate(value) {
  if (!value) return "-";
  const [year, month, day] = String(value).split("-");
  return `${day}.${month}.${year}`;
}

function healthClass(value) {
  if (value === null || value === undefined) return "";
  if (Number(value) >= 100) return "good";
  return "bad";
}

function average(rows, field) {
  const vals = rows.map((row) => Number(row[field])).filter((value) => Number.isFinite(value));
  if (!vals.length) return null;
  return vals.reduce((sum, value) => sum + value, 0) / vals.length;
}

function sum(rows, field) {
  return rows.reduce((acc, row) => acc + (Number(row[field]) || 0), 0);
}

async function loadMetrics(start, end) {
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  const response = await fetch(`/api/metrics?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Failed to load metrics");
  return payload;
}

function render(payload) {
  sourceEl.textContent = `${payload.source} · available ${payload.available_min} to ${payload.available_max}`;
  startInput.min = payload.available_min;
  startInput.max = payload.available_max;
  endInput.min = payload.available_min;
  endInput.max = payload.available_max;
  if (!startInput.value) startInput.value = payload.available_min;
  if (!endInput.value) endInput.value = payload.available_max;

  const rows = payload.rows || [];
  rangeNoteEl.textContent = `${payload.selected_start} to ${payload.selected_end} · ${rows.length} rows`;
  kpiHealth.textContent = fmt(average(rows, "health_score"));
  kpiSearches.textContent = fmt(average(rows, "mean_searches_to_cart"));
  kpiSessions.textContent = fmtInt(sum(rows, "n_sessions"));
  kpiConverted.textContent = fmtInt(sum(rows, "converted_sessions"));

  rowsEl.innerHTML = rows.map((row) => `
    <tr>
      <td>${fmtDate(row.date)}</td>
      <td class="${healthClass(row.health_score)}">${fmt(row.health_score)}</td>
      <td>${fmt(row.group_quality)}</td>
      <td>${fmt(row.group_engagement)}</td>
      <td>${fmt(row.group_discovery)}</td>
      <td>${fmt(row.mean_searches_to_cart)}</td>
      <td>${fmt(row.mean_unique_queries_to_cart)}</td>
      <td>${fmt(row.mean_time_to_cart_s)}</td>
      <td>${fmtInt(row.n_sessions)}</td>
      <td>${fmtInt(row.converted_sessions)}</td>
    </tr>
  `).join("");

  if (!rows.length) {
    rowsEl.innerHTML = `<tr><td colspan="10">No rows for selected range.</td></tr>`;
  }
}

async function refresh() {
  try {
    render(await loadMetrics(startInput.value, endInput.value));
  } catch (error) {
    rowsEl.innerHTML = `<tr><td colspan="10">${error.message}</td></tr>`;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  refresh();
});

refresh();

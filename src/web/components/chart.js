import { fmt } from "../lib/fmt.js";

const COLORS = {
  blue: "#2563eb",
  blueSoft: "rgba(37, 99, 235, 0.08)",
  red: "#dc2626",
  green: "#16a34a",
  ink: "#111827",
  inkMuted: "#6b7280",
  rule: "#e5e7eb",
  rule2: "#d1d5db",
};

export function makeHealthChart(canvas, rows, opts = {}) {
  const labels = rows.map((r) => r.date);
  const health = rows.map((r) => r.health_score);
  const strat = rows.map((r) => r.stratified_health_score);
  const baseline = labels.map(() => 100);

  const pointColors = rows.map((r) => (r.has_alert ? COLORS.red : COLORS.blue));
  const pointRadii = rows.map((r) => (r.has_alert ? 4.5 : 2));

  const ctx = canvas.getContext("2d");
  return new window.Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Health",
          data: health,
          borderColor: COLORS.blue,
          backgroundColor: COLORS.blueSoft,
          fill: true,
          tension: 0.25,
          borderWidth: 1.5,
          pointBackgroundColor: pointColors,
          pointBorderColor: pointColors,
          pointRadius: pointRadii,
          pointHoverRadius: 6,
        },
        {
          label: "Stratified",
          data: strat,
          borderColor: COLORS.inkMuted,
          borderDash: [3, 4],
          borderWidth: 1.2,
          backgroundColor: "transparent",
          tension: 0.25,
          pointRadius: 0,
        },
        {
          label: "Baseline (100)",
          data: baseline,
          borderColor: COLORS.rule2,
          borderWidth: 1,
          backgroundColor: "transparent",
          pointRadius: 0,
          tension: 0,
        },
      ],
    },
    options: {
      maintainAspectRatio: false,
      responsive: true,
      onClick: (evt, elements) => {
        if (!elements.length || !opts.onPick) return;
        const date = labels[elements[0].index];
        opts.onPick(date);
      },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            color: COLORS.inkMuted,
            font: { family: "Inter, sans-serif", size: 11 },
            boxWidth: 12, boxHeight: 2, padding: 16, usePointStyle: false,
          },
        },
        tooltip: {
          backgroundColor: "#ffffff",
          titleColor: COLORS.ink,
          bodyColor: COLORS.ink,
          borderColor: COLORS.rule,
          borderWidth: 1,
          padding: 10,
          cornerRadius: 4,
          titleFont: { family: "Inter, sans-serif", weight: 600, size: 12 },
          bodyFont: { family: "JetBrains Mono, monospace", size: 11 },
          callbacks: {
            afterBody(items) {
              const idx = items[0].dataIndex;
              const r = rows[idx];
              const tip = [];
              if (r.has_alert) {
                tip.push(`alerts: ${r.n_anomalies_active}`);
                if (r.alert_features) tip.push(`  ${r.alert_features}`);
              }
              tip.push(`drift  ${fmt.delta(r.traffic_drift_signal)}`);
              return tip;
            },
          },
        },
      },
      scales: {
        y: {
          grid: { color: COLORS.rule, drawTicks: false, lineWidth: 1 },
          ticks: {
            color: COLORS.inkMuted,
            font: { family: "JetBrains Mono, monospace", size: 10 },
            callback: (v) => v.toFixed(0),
            stepSize: 25,
          },
          border: { display: false },
        },
        x: {
          grid: { display: false },
          ticks: {
            color: COLORS.inkMuted,
            font: { family: "JetBrains Mono, monospace", size: 10 },
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 14,
          },
          border: { color: COLORS.rule },
        },
      },
    },
  });
}

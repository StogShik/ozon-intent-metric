const nf = new Intl.NumberFormat("ru-RU");

export const fmt = {
  num(v, digits = 1) {
    if (v == null) return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return "—";
    return n.toFixed(digits);
  },

  pct(v, digits = 1) {
    if (v == null) return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return "—";
    return (n * 100).toFixed(digits) + "%";
  },

  delta(v, digits = 2) {
    if (v == null) return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return "—";
    const s = n.toFixed(digits);
    return n >= 0 ? "+" + s : s;
  },

  int(v) {
    if (v == null) return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return "—";
    return nf.format(Math.round(n));
  },

  duration(seconds) {
    if (seconds == null) return "—";
    const s = Number(seconds);
    if (Number.isNaN(s)) return "—";
    if (s < 60) return s.toFixed(0) + "s";
    if (s < 3600) return (s / 60).toFixed(1) + "m";
    return (s / 3600).toFixed(1) + "h";
  },

  signClass(v, opts = {}) {
    const inv = opts.invert === true;
    if (v == null) return "";
    const n = Number(v);
    if (Number.isNaN(n) || n === 0) return "";
    if (inv) return n > 0 ? "is-bad" : "is-good";
    return n > 0 ? "is-good" : "is-bad";
  },

  healthClass(v, bandsLow = 95, bandsHigh = 105) {
    if (v == null) return "";
    const n = Number(v);
    if (Number.isNaN(n)) return "";
    if (n < bandsLow) return "is-bad";
    if (n > bandsHigh) return "is-good";
    return "";
  },
};

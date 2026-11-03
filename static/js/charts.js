// Small, dependency-free SVG charts for Sprint 12: inline sparklines and a
// money-flow (Sankey) diagram. Both draw with the app's theme tokens
// (var(--pos), var(--brand), ...) so they read correctly in day and night.
// Pure string builders where it helps, so the maths can be unit-tested.

const n2 = (x) => Math.round(x * 100) / 100;

// --- Sparkline -------------------------------------------------------------
// Build the path data for a sparkline over `vals` inside a w×h box.
export function sparkPath(vals, w, h, pad = 3) {
  const xs = vals.map(Number);
  const mn = Math.min(...xs), mx = Math.max(...xs), rng = (mx - mn) || 1;
  const n = xs.length;
  const x = (i) => (n === 1 ? w / 2 : pad + (i * (w - 2 * pad)) / (n - 1));
  const y = (v) => h - pad - ((v - mn) / rng) * (h - 2 * pad);
  let line = `M${n2(x(0))} ${n2(y(xs[0]))}`;
  for (let i = 1; i < n; i++) line += ` L${n2(x(i))} ${n2(y(xs[i]))}`;
  const area = `${line} L${n2(x(n - 1))} ${n2(h - pad)} L${n2(x(0))} ${n2(h - pad)} Z`;
  return { line, area, lastX: x(n - 1), lastY: y(xs[n - 1]) };
}

let sparkSeq = 0;
// Render a sparkline into an <svg class="spark"> element. `dir` picks the tint:
// "up" = green, "down" = red, anything else = muted. Pass it explicitly so the
// colour reflects whether the trend is good news, which depends on the metric.
export function sparkline(el, vals, { dir = "up", w = 88, h = 28, pad = 3 } = {}) {
  if (!el || !vals || vals.length < 2) { if (el) el.innerHTML = ""; return; }
  const col = dir === "up" ? "var(--pos)" : dir === "down" ? "var(--neg)" : "var(--muted)";
  const p = sparkPath(vals, w, h, pad);
  const gid = "spk" + (sparkSeq++);
  el.setAttribute("viewBox", `0 0 ${w} ${h}`);
  el.setAttribute("preserveAspectRatio", "none");
  el.setAttribute("aria-hidden", "true");
  el.innerHTML =
    `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="${col}" stop-opacity="0.28"/>` +
    `<stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>` +
    `<path d="${p.area}" fill="url(#${gid})"/>` +
    `<path d="${p.line}" fill="none" stroke="${col}" stroke-width="1.6" ` +
    `stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>` +
    `<circle cx="${n2(p.lastX)}" cy="${n2(p.lastY)}" r="2.2" fill="${col}"/>`;
}

// Direction from a series: up / down / flat, with a small deadband.
export function trendDir(vals) {
  if (!vals || vals.length < 2) return "flat";
  const first = Number(vals[0]), last = Number(vals[vals.length - 1]);
  const base = Math.abs(first) || 1;
  const change = (last - first) / base;
  if (change > 0.01) return "up";
  if (change < -0.01) return "down";
  return "flat";
}

// --- Sankey (money flow) ----------------------------------------------------
// columns: array of columns, each an array of nodes {id, label, sub?, value, color}.
// links:   array of {from, to, value, color?}.
// Node y-positions are stacked proportionally to value using one shared scale,
// so band thickness reads as the amount across the whole diagram.
export function layoutSankey(columns, links, opts = {}) {
  const W = opts.width || 760, H = opts.height || 360;
  const pad = opts.pad ?? 16, nodeW = opts.nodeW ?? 20, gap = opts.gap ?? 10;
  const cols = columns.length;
  const colX = opts.colX || columns.map((_, i) => pad + (i * (W - 2 * pad - nodeW)) / Math.max(cols - 1, 1));
  const availH = H - 2 * pad;
  // One scale (px per unit) that makes every column fit, gaps included.
  let scale = Infinity;
  columns.forEach((col) => {
    const total = col.reduce((s, nd) => s + Math.max(nd.value, 0), 0) || 1;
    const room = availH - (col.length - 1) * gap;
    scale = Math.min(scale, room / total);
  });
  if (!isFinite(scale) || scale <= 0) scale = 1;

  const byId = {};
  columns.forEach((col, ci) => {
    const total = col.reduce((s, nd) => s + Math.max(nd.value, 0), 0);
    const contentH = total * scale + (col.length - 1) * gap;
    let cur = pad + (availH - contentH) / 2;
    col.forEach((nd) => {
      const h = Math.max(nd.value, 0) * scale;
      const node = { ...nd, ci, x: colX[ci], y0: cur, y1: cur + h, h, _out: cur, _in: cur };
      byId[nd.id] = node;
      cur += h + gap;
    });
  });

  // Keep bands tidy: order by source column, then source then target position.
  const ordered = [...links].sort((a, b) => {
    const sa = byId[a.from], sb = byId[b.from], ta = byId[a.to], tb = byId[b.to];
    return (sa.ci - sb.ci) || (sa.y0 - sb.y0) || (ta.y0 - tb.y0);
  });
  const bands = ordered.map((lk) => {
    const s = byId[lk.from], t = byId[lk.to];
    const h = lk.value * scale;
    const b = { x1: s.x + nodeW, yt1: s._out, x2: t.x, yt2: t._in, h, color: lk.color || s.color };
    s._out += h; t._in += h;
    return b;
  });
  return { nodes: Object.values(byId), bands, nodeW, W, H };
}

export function sankey(el, columns, links, opts = {}) {
  const { nodes, bands, nodeW, W, H } = layoutSankey(columns, links, opts);
  const cols = columns.length;
  const band = (b) => {
    const mid = (b.x1 + b.x2) / 2, yb1 = b.yt1 + b.h, yb2 = b.yt2 + b.h;
    return `<path d="M${n2(b.x1)} ${n2(b.yt1)} C${n2(mid)} ${n2(b.yt1)},${n2(mid)} ${n2(b.yt2)},${n2(b.x2)} ${n2(b.yt2)} ` +
      `L${n2(b.x2)} ${n2(yb2)} C${n2(mid)} ${n2(yb2)},${n2(mid)} ${n2(yb1)},${n2(b.x1)} ${n2(yb1)} Z" ` +
      `fill="${b.color}" opacity="0.42"/>`;
  };
  const label = (nd) => {
    const last = nd.ci === cols - 1, first = nd.ci === 0;
    const midY = (nd.y0 + nd.y1) / 2;
    if (first) {
      return `<text x="${n2(nd.x - 8)}" y="${n2(midY - 1)}" text-anchor="end" font-size="12" font-weight="700" font-family="Inter,sans-serif" fill="var(--ink)">${esc(nd.label)}</text>` +
        (nd.sub ? `<text x="${n2(nd.x - 8)}" y="${n2(midY + 14)}" text-anchor="end" font-size="11" font-family="Inter,sans-serif" fill="var(--muted)">${esc(nd.sub)}</text>` : "");
    }
    if (last) {
      return `<text x="${n2(nd.x + nodeW + 8)}" y="${n2(midY + 4)}" text-anchor="start" font-size="11.5" font-family="Inter,sans-serif" fill="var(--ink)">${esc(nd.label)}${nd.sub ? "  " + esc(nd.sub) : ""}</text>`;
    }
    return `<text x="${n2(nd.x + nodeW + 8)}" y="${n2(midY - 2)}" text-anchor="start" font-size="12" font-weight="600" font-family="Inter,sans-serif" fill="var(--ink)">${esc(nd.label)}</text>` +
      (nd.sub ? `<text x="${n2(nd.x + nodeW + 8)}" y="${n2(midY + 13)}" text-anchor="start" font-size="11" font-family="Inter,sans-serif" fill="var(--muted)">${esc(nd.sub)}</text>` : "");
  };
  const rect = (nd) => `<rect x="${n2(nd.x)}" y="${n2(nd.y0)}" width="${nodeW}" height="${n2(Math.max(nd.h, 1))}" rx="3" fill="${nd.color}" opacity="0.92"/>`;
  el.setAttribute("viewBox", `0 0 ${W} ${H}`);
  el.setAttribute("role", "img");
  el.innerHTML = bands.map(band).join("") + nodes.map(rect).join("") + nodes.map(label).join("");
}

function esc(s) {
  return String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

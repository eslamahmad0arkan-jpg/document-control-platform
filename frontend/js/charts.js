const PALETTE = ["#0a84ff", "#8b5cf6", "#2fbf71", "#f5a623", "#e5484d", "#14b8a6", "#f46f9a", "#7dd3fc"];

function color(i) { return PALETTE[i % PALETTE.length]; }

export function bars(elWrap, items, { color: c = "#0a84ff", height = 170, suffix = "" } = {}) {
  const max = Math.max(1, ...items.map(i => i.value));
  const wrap = document.createElement("div");
  wrap.className = "chart";
  wrap.style.cssText = `display:flex;align-items:flex-end;gap:10px;height:${height}px;padding-top:8px;border-bottom:1px solid var(--border);`;
  for (const it of items) {
    const h = Math.round((it.value / max) * (height - 34));
    const col = document.createElement("div");
    col.style.cssText = "flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0;";
    const value = document.createElement("div");
    value.textContent = it.value;
    value.style.cssText = "font-size:11px;color:var(--muted);font-weight:600;";
    const bar = document.createElement("div");
    bar.style.cssText = `width:100%;background:${c};border-radius:5px 5px 0 0;height:${Math.max(2, h)}px;opacity:.9;transition:height .3s;`;
    if (it.value === max && max > 0) bar.style.background = "#fff";
    const label = document.createElement("div");
    label.textContent = String(it.label).slice(0, 12) + suffix;
    label.style.cssText = "font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;";
    col.append(value, bar, label);
    wrap.append(col);
  }
  elWrap.append(wrap);
  return wrap;
}

export function donut(elWrap, items, { size = 170, thickness = 26, centerLabel = "" } = {}) {
  const total = items.reduce((s, i) => s + i.value, 0);
  const wrap = document.createElement("div");
  wrap.className = "chart";
  wrap.style.cssText = "display:flex;flex-direction:column;align-items:center;gap:8px;";
  if (!total) {
    wrap.append(emptyChart());
  } else {
    const svgNs = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.style.width = size + "px";
    svg.style.height = size + "px";
    svg.style.transform = "rotate(-90deg)";
    const r = (size - thickness) / 2 - 2;
    const cx = size / 2, cy = size / 2;
    const arc = 2 * Math.PI * r;
    let offset = 0;
    items.forEach((it, i) => {
      const frac = it.value / total;
      const circle = document.createElementNS(svgNs, "circle");
      circle.setAttribute("cx", cx);
      circle.setAttribute("cy", cy);
      circle.setAttribute("r", r);
      circle.setAttribute("fill", "none");
      circle.setAttribute("stroke", color(i));
      circle.setAttribute("stroke-width", thickness);
      circle.setAttribute("stroke-dasharray", `${frac * arc} ${arc}`);
      circle.setAttribute("stroke-dashoffset", -offset * arc);
      circle.setAttribute("stroke-linecap", "butt");
      svg.append(circle);
      offset += frac;
    });
    wrap.append(svg);
  }
  if (centerLabel) {
    const cl = document.createElement("div");
    cl.textContent = centerLabel;
    cl.style.cssText = "font-size:13px;font-weight:700;margin-top:8px;";
    wrap.append(cl);
  }
  const legend = document.createElement("div");
  legend.className = "legend";
  for (const it of items) {
    legend.append(elLegend(color(items.indexOf(it)), `${it.label} (${it.value})`));
  }
  wrap.append(legend);
  elWrap.append(wrap);
  return wrap;
}

export function elLegend(color, label) {
  const li = document.createElement("div");
  li.className = "li";
  const sw = document.createElement("span");
  sw.className = "sw";
  sw.style.background = color;
  li.append(sw, label);
  return li;
}

function emptyChart() {
  const d = document.createElement("div");
  d.textContent = "No data";
  d.style.cssText = "color:var(--muted);font-size:13px;height:80px;display:grid;place-items:center;";
  return d;
}

export function PALETTE_KEYS(items) { return items; }
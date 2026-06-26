"use strict";

let activeTab = "system";
let systemTimer = null;

// SDR modes a tab maps to (tabs not listed need no receiver).
const SDR_TAB_MODE = { spectrum: "spectrum", adsb: "adsb" };

// ----- formatting helpers -------------------------------------------------- //
function fmtBytes(n) {
  if (n == null) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtUptime(s) {
  if (s == null) return "-";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const parts = [];
  if (d) parts.push(`${d}d`);
  if (h || d) parts.push(`${h}h`);
  parts.push(`${m}m`);
  return parts.join(" ");
}

function fmtMHz(hz) { return (hz / 1e6).toFixed(3) + " MHz"; }

// ----- system tab ---------------------------------------------------------- //
const MAXPTS = 60;                 // ~2 min of history at 2s polling
const HIST = { cpu: [], mem: [], temp: [], power: [] };
let systemBuilt = false;

function pushHist(key, value) {
  if (value == null) return;
  const a = HIST[key];
  a.push(value);
  if (a.length > MAXPTS) a.shift();
}

// Task-manager style sparkline: filled area under a line, newest sample at the right.
function drawSpark(canvas, data, color, lo, hi) {
  fitCanvasWidth(canvas);
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (data.length < 2) return;
  let mn = lo, mx = hi;
  if (mn == null) {
    mn = Math.min(...data); mx = Math.max(...data);
    const pad = (mx - mn) * 0.2 || 1; mn -= pad; mx += pad;
  }
  const span = (mx - mn) || 1;
  const xat = i => ((MAXPTS - data.length + i) / (MAXPTS - 1)) * w;
  const yat = v => h - ((v - mn) / span) * (h - 3) - 1.5;
  const pts = data.map((v, i) => [xat(i), yat(v)]);

  ctx.beginPath();
  ctx.moveTo(pts[0][0], h);
  pts.forEach(p => ctx.lineTo(p[0], p[1]));
  ctx.lineTo(pts[pts.length - 1][0], h);
  ctx.closePath();
  ctx.fillStyle = color + "22";
  ctx.fill();

  ctx.beginPath();
  pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

function metricCard(key, title) {
  return `<div class="card"><h3>${title}</h3>`
    + `<div class="value" id="v-${key}">-</div>`
    + `<div class="sub" id="s-${key}"></div>`
    + `<canvas class="spark" id="spark-${key}" height="40"></canvas></div>`;
}

function plainCard(key, title, withBar) {
  const bar = withBar ? `<div class="bar"><span id="bar-${key}"></span></div>` : "";
  return `<div class="card"><h3>${title}</h3>`
    + `<div class="value" id="v-${key}">-</div>`
    + `<div class="sub" id="s-${key}"></div>${bar}</div>`;
}

function buildSystemCards() {
  document.getElementById("system-cards").innerHTML = [
    plainCard("host", "Host"),
    plainCard("net", "Network"),
    metricCard("cpu", "CPU"),
    metricCard("temp", "CPU temp"),
    metricCard("mem", "Memory"),
    metricCard("power", "Power"),
    plainCard("disk", "Disk (/)", true),
    plainCard("uptime", "Uptime"),
  ].join("");
  systemBuilt = true;
}

function setText(id, text) { const el = document.getElementById(id); if (el) el.innerHTML = text; }

function updateSystemCards(d) {
  const addrs = d.addresses.map(a => `${a.address} (${a.interface})`).join(", ") || "-";
  const load = d.load_avg.map(x => x == null ? "-" : x.toFixed(2)).join(" / ");

  setText("v-host", d.hostname); setText("s-host", `${d.os} (${d.machine})`);
  setText("v-net", addrs); setText("s-net", "IPv4 addresses");
  setText("v-cpu", `${d.cpu_percent.toFixed(0)}%`); setText("s-cpu", `${d.cpu_count} cores · load ${load}`);
  setText("v-temp", d.cpu_temp_c == null ? "n/a" : `${d.cpu_temp_c} &deg;C`);
  setText("v-mem", `${d.memory.percent.toFixed(0)}%`);
  setText("s-mem", `${fmtBytes(d.memory.used)} / ${fmtBytes(d.memory.total)}`);
  setText("v-power", d.power_watts == null ? "n/a" : `${d.power_watts.toFixed(2)} W`);
  setText("v-disk", `${d.disk.percent.toFixed(0)}%`);
  setText("s-disk", `${fmtBytes(d.disk.used)} / ${fmtBytes(d.disk.total)}`);
  const db = document.getElementById("bar-disk"); if (db) db.style.width = `${Math.min(100, d.disk.percent)}%`;
  setText("v-uptime", fmtUptime(d.uptime_seconds));

  pushHist("cpu", d.cpu_percent);
  pushHist("mem", d.memory.percent);
  pushHist("temp", d.cpu_temp_c);
  pushHist("power", d.power_watts);

  drawSpark(document.getElementById("spark-cpu"), HIST.cpu, "#4cc2ff", 0, 100);
  drawSpark(document.getElementById("spark-mem"), HIST.mem, "#3fb950", 0, 100);
  drawSpark(document.getElementById("spark-temp"), HIST.temp, "#f0883e");
  drawSpark(document.getElementById("spark-power"), HIST.power, "#d2a8ff");
}

async function loadSystem() {
  try {
    const r = await fetch("/api/system");
    const d = await r.json();
    if (!systemBuilt) buildSystemCards();
    updateSystemCards(d);
  } catch (e) {
    if (!systemBuilt) {
      document.getElementById("system-cards").innerHTML =
        `<p class="muted">Failed to load system info: ${e}</p>`;
    }
  }
}

// ----- SDR status tab ------------------------------------------------------ //
async function loadSdr() {
  const el = document.getElementById("sdr-status");
  el.innerHTML = `<p class="muted">Probing the dongle…</p>`;
  try {
    const r = await fetch("/api/sdr/status");
    const d = await r.json();
    const pill = d.available
      ? `<span class="pill good">connected</span>`
      : `<span class="pill bad">not detected</span>`;
    let html = `<p class="status-line">Dongle: ${pill}</p>`;
    if (d.in_use_by) html += `<p class="status-line">In use by: ${d.in_use_by}${d.simulated ? " (simulated)" : ""}</p>`;
    if (d.device) html += `<p class="status-line">Device: ${d.device}</p>`;
    if (d.tuner) html += `<p class="status-line">Tuner: ${d.tuner}</p>`;
    if (d.error) html += `<p class="status-line muted">${d.error}</p>`;
    if (d.raw) html += `<pre>${d.raw.replace(/</g, "&lt;")}</pre>`;
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<p class="muted">Failed to probe SDR: ${e}</p>`;
  }
}

// ----- SDR mode control ---------------------------------------------------- //
async function setSdrMode(mode) {
  try {
    await fetch("/api/sdr/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: mode }),
    });
  } catch (e) { /* ignore; the websocket reports state */ }
}

// ----- spectrum rendering -------------------------------------------------- //
function dbColor(db) {
  // Map ~ -35..+10 dB to blue -> cyan -> yellow -> red.
  const t = Math.max(0, Math.min(1, (db + 35) / 45));
  const r = Math.floor(255 * Math.min(1, Math.max(0, t * 2 - 0.6)));
  const g = Math.floor(255 * Math.min(1, Math.max(0, t * 1.8)));
  const b = Math.floor(255 * Math.min(1, Math.max(0, 1 - t * 1.6)));
  return `rgb(${r},${g},${b})`;
}

function fitCanvasWidth(canvas) {
  const w = canvas.clientWidth || canvas.parentElement.clientWidth;
  if (w && canvas.width !== w) canvas.width = w;
}

function renderSpectrum(frame) {
  const line = document.getElementById("spectrum-line");
  const wf = document.getElementById("waterfall");
  fitCanvasWidth(line);
  fitCanvasWidth(wf);
  const bins = frame.bins;
  const n = bins.length;
  if (!n) return;

  // line chart of the current sweep
  const lc = line.getContext("2d");
  lc.clearRect(0, 0, line.width, line.height);
  lc.strokeStyle = "#4cc2ff";
  lc.beginPath();
  let min = Infinity, max = -Infinity;
  for (const v of bins) { if (v < min) min = v; if (v > max) max = v; }
  const span = (max - min) || 1;
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * line.width;
    const y = line.height - ((bins[i] - min) / span) * (line.height - 4) - 2;
    i === 0 ? lc.moveTo(x, y) : lc.lineTo(x, y);
  }
  lc.stroke();

  // waterfall: scroll down one row, draw new row on top
  const wc = wf.getContext("2d");
  wc.drawImage(wf, 0, 1);
  const row = wc.createImageData(wf.width, 1);
  for (let x = 0; x < wf.width; x++) {
    const v = bins[Math.floor((x / wf.width) * n)];
    const c = dbColor(v).match(/\d+/g);
    const o = x * 4;
    row.data[o] = +c[0]; row.data[o + 1] = +c[1]; row.data[o + 2] = +c[2]; row.data[o + 3] = 255;
  }
  wc.putImageData(row, 0, 0);

  document.getElementById("spectrum-range").textContent =
    `${fmtMHz(frame.f_start)} – ${fmtMHz(frame.f_stop)} · ${n} bins`;
}

function setSpectrumState(mode, simulated) {
  document.getElementById("sim-banner").classList.toggle("hidden", !(mode === "spectrum" && simulated));
  const el = document.getElementById("spectrum-state");
  if (mode === "spectrum") el.textContent = simulated ? "live (simulated)" : "live";
  else el.textContent = "stopped";
}

// ----- ADS-B map ----------------------------------------------------------- //
let map = null, homeMarker = null;
let rxLat = 50.05, rxLon = 8.60;
const planeMarkers = {};

function planeIcon(track) {
  // Plane silhouette pointing north, rotated by heading.
  const path = "M12 2 L13 9 L21 14 L21 16 L13 12 L13 19 L16 21 L16 22 L12 20 "
    + "L8 22 L8 21 L11 19 L11 12 L3 16 L3 14 L11 9 Z";
  return L.divIcon({
    className: "",
    html: `<svg class="plane-icon" width="22" height="22" viewBox="0 0 24 24" `
      + `style="transform:rotate(${track}deg)"><path fill="currentColor" d="${path}"/></svg>`,
    iconSize: [22, 22], iconAnchor: [11, 11],
  });
}

function baseLayers() {
  const osmAttr = "&copy; OpenStreetMap contributors";
  const cartoAttr = osmAttr + " &copy; CARTO";
  return {
    // Dark, minimal: roads/water/place names, no POI clutter.
    "Dark (minimal)": L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 20, subdomains: "abcd", attribution: cartoAttr }),
    "Light (minimal)": L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 20, subdomains: "abcd", attribution: cartoAttr }),
    "Topographic": L.tileLayer(
      "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
      { maxZoom: 17, subdomains: "abc",
        attribution: "&copy; OpenTopoMap (CC-BY-SA), " + osmAttr }),
    "OSM": L.tileLayer(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { maxZoom: 19, attribution: osmAttr }),
  };
}

function initMap() {
  if (map) return;
  map = L.map("map", { zoomControl: true }).setView([rxLat, rxLon], 7);
  const bases = baseLayers();
  const saved = localStorage.getItem("basemap");
  const initial = bases[saved] ? saved : "Dark (minimal)";
  bases[initial].addTo(map);
  L.control.layers(bases).addTo(map);
  map.on("baselayerchange", (e) => localStorage.setItem("basemap", e.name));
  homeMarker = L.circleMarker([rxLat, rxLon], {
    radius: 5, color: "#f0883e", fillColor: "#f0883e", fillOpacity: 1,
  }).addTo(map).bindTooltip("Receiver");
}

function setAdsbState(simulated) {
  document.getElementById("adsb-banner").classList.toggle("hidden", !simulated);
  document.getElementById("adsb-state").textContent = simulated ? "live (simulated)" : "live";
}

function renderAdsb(frame) {
  if (!map) return;
  const seen = {};
  (frame.aircraft || []).forEach(a => {
    if (a.lat == null || a.lon == null) return;
    seen[a.hex] = true;
    let m = planeMarkers[a.hex];
    if (!m) {
      m = L.marker([a.lat, a.lon], { icon: planeIcon(a.track) }).addTo(map);
      m.bindTooltip("");
      planeMarkers[a.hex] = m;
    } else {
      m.setLatLng([a.lat, a.lon]);
      m.setIcon(planeIcon(a.track));
    }
    m.setTooltipContent(`${a.flight || a.hex} · ${a.alt} ft · ${a.gs} kt`);
  });
  for (const hex in planeMarkers) {
    if (!seen[hex]) { map.removeLayer(planeMarkers[hex]); delete planeMarkers[hex]; }
  }

  const tbody = document.querySelector("#adsb-table tbody");
  tbody.innerHTML = (frame.aircraft || []).map(a =>
    `<tr><td>${a.flight || a.hex}</td><td>${a.alt}</td><td>${a.gs}</td>`
    + `<td>${Math.round(a.track)}&deg;</td></tr>`).join("");
  document.getElementById("adsb-count").textContent =
    `${(frame.aircraft || []).length} aircraft`;
}

// ----- websocket ----------------------------------------------------------- //
let ws = null;
let currentMode = null, currentSim = false;

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "mode") {
      currentMode = msg.mode; currentSim = msg.simulated;
      if (msg.rx) {
        rxLat = msg.rx.lat; rxLon = msg.rx.lon;
        if (homeMarker) homeMarker.setLatLng([rxLat, rxLon]);
      }
      setSpectrumState(msg.mode, msg.simulated);
      setAdsbState(msg.mode === "adsb" && msg.simulated);
    } else if (msg.type === "frame" && msg.mode === "spectrum") {
      currentSim = msg.simulated;
      if (activeTab === "spectrum") {
        setSpectrumState("spectrum", msg.simulated);
        renderSpectrum(msg);
      }
    } else if (msg.type === "frame" && msg.mode === "adsb") {
      currentSim = msg.simulated;
      if (activeTab === "adsb") {
        setAdsbState(msg.simulated);
        renderAdsb(msg);
      }
    }
  };
  ws.onclose = () => { ws = null; setTimeout(connectWs, 2000); };
}

// ----- tabs ---------------------------------------------------------------- //
function setTab(name) {
  const prev = activeTab;
  activeTab = name;
  document.querySelectorAll(".tab").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(p =>
    p.classList.toggle("active", p.id === name));

  if (systemTimer) { clearInterval(systemTimer); systemTimer = null; }

  // Start/stop the receiver as we move between SDR tabs.
  const prevMode = SDR_TAB_MODE[prev];
  const nextMode = SDR_TAB_MODE[name];
  if (prevMode && prevMode !== nextMode) setSdrMode("off");
  if (nextMode) setSdrMode(nextMode);

  if (name === "system") {
    loadSystem();
    systemTimer = setInterval(loadSystem, 2000);
  } else if (name === "sdr") {
    loadSdr();
  } else if (name === "adsb") {
    initMap();
    // The map container was just shown; let Leaflet recompute its size.
    setTimeout(() => map && map.invalidateSize(), 50);
  }
}

document.querySelectorAll(".tab").forEach(b =>
  b.addEventListener("click", () => setTab(b.dataset.tab)));

function tick() {
  document.getElementById("clock").textContent = new Date().toLocaleString();
}
setInterval(tick, 1000);
tick();

connectWs();
setTab("system");

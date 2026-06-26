"use strict";

let activeTab = "system";
let systemTimer = null;

// SDR modes a tab maps to (tabs not listed need no receiver).
const SDR_TAB_MODE = { spectrum: "spectrum" };

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
function card(title, value, sub, barPercent) {
  const bar = barPercent != null
    ? `<div class="bar"><span style="width:${Math.min(100, barPercent)}%"></span></div>`
    : "";
  return `<div class="card"><h3>${title}</h3><div class="value">${value}</div>`
    + (sub ? `<div class="sub">${sub}</div>` : "") + bar + `</div>`;
}

async function loadSystem() {
  try {
    const r = await fetch("/api/system");
    const d = await r.json();
    const addrs = d.addresses.map(a => `${a.address} (${a.interface})`).join(", ") || "-";
    const load = d.load_avg.map(x => x == null ? "-" : x.toFixed(2)).join(" / ");
    const temp = d.cpu_temp_c == null ? "-" : `${d.cpu_temp_c} &deg;C`;
    document.getElementById("system-cards").innerHTML = [
      card("Host", d.hostname, `${d.os} (${d.machine})`),
      card("Network", addrs, "IPv4 addresses"),
      card("CPU", `${d.cpu_percent.toFixed(0)}%`, `${d.cpu_count} cores · load ${load}`, d.cpu_percent),
      card("CPU temp", temp, ""),
      card("Memory", `${d.memory.percent.toFixed(0)}%`, `${fmtBytes(d.memory.used)} / ${fmtBytes(d.memory.total)}`, d.memory.percent),
      card("Disk (/)", `${d.disk.percent.toFixed(0)}%`, `${fmtBytes(d.disk.used)} / ${fmtBytes(d.disk.total)}`, d.disk.percent),
      card("Uptime", fmtUptime(d.uptime_seconds), ""),
    ].join("");
  } catch (e) {
    document.getElementById("system-cards").innerHTML =
      `<p class="muted">Failed to load system info: ${e}</p>`;
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
      setSpectrumState(msg.mode, msg.simulated);
    } else if (msg.type === "frame" && msg.mode === "spectrum") {
      currentSim = msg.simulated;
      if (activeTab === "spectrum") {
        setSpectrumState("spectrum", msg.simulated);
        renderSpectrum(msg);
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

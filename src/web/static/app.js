"use strict";

let activeTab = "system";
let systemTimer = null;

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
    if (d.device) html += `<p class="status-line">Device: ${d.device}</p>`;
    if (d.tuner) html += `<p class="status-line">Tuner: ${d.tuner}</p>`;
    if (d.error) html += `<p class="status-line muted">${d.error}</p>`;
    if (d.raw) html += `<pre>${d.raw.replace(/</g, "&lt;")}</pre>`;
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<p class="muted">Failed to probe SDR: ${e}</p>`;
  }
}

function setTab(name) {
  activeTab = name;
  document.querySelectorAll(".tab").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(p =>
    p.classList.toggle("active", p.id === name));

  if (systemTimer) { clearInterval(systemTimer); systemTimer = null; }
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

setTab("system");

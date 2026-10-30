/* Movite — client web */
"use strict";

// ------------------------------------------------------------------ stato
const S = {
  routes: [],
  stops: null,
  vehicles: new Map(),        // id -> { d, marker, from, to, t0 }
  moving: new Set(),
  filter: { 0: true, 1: true, 3: true },
  busEst: false,
  tab: "explore",
  stack: [],
  focusRoutes: null,         // Set di linee in evidenza (le altre vengono attenuate)
  selVehicle: null,
  follow: false,
  me: null,
  skew: 0,
  feeds: {},
  plan: { from: null, to: null, when: null, arrive: false, modes: { 1: true, 0: true, 3: true, 2: true },
          rail: store("movite-rail") === "1", gps: store("movite-gps") === "1", results: null, sel: 0, picking: null },
  alerts: [],
};

const MODE_NAMES = { 0: "Tram", 1: "Metro", 2: "Treno", 3: "Bus", 7: "Funicolare" };
const TYPE_ORDER = { 1: 0, 0: 1, 2: 2, 3: 3 };

// ------------------------------------------------------------------ utilità
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const now = () => Date.now() / 1000 + S.skew;
const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Rome" });
const mins = (t) => Math.round((t - now()) / 60);
const fmtDur = (s) => { const m = Math.round(s / 60); return m < 60 ? `${m}<small>min</small>` : `${Math.floor(m / 60)}<small>h</small> ${m % 60}<small>min</small>`; };
const fmtDist = (m) => (m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`);
const ago = (ts) => { const s = Math.max(0, Math.round(now() - ts)); return s < 60 ? `${s} s fa` : `${Math.round(s / 60)} min fa`; };
const titleCase = (s) => String(s || "").toLowerCase().replace(/(^|[\s/(\-'.])([a-zà-ù])/g, (m, a, b) => a + b.toUpperCase()).replace(/\((m[abc])/gi, (m) => m.toUpperCase()).replace(/\b(Fs|Ma|Mb|Mc|Rl)\b/g, (m) => m.toUpperCase());
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const isMobile = () => matchMedia("(max-width: 760px)").matches;

async function api(path) {
  const r = await fetch(path);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 2600);
}

function store(key, val) {
  try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch (e) { return null; }
}

// ------------------------------------------------------------------ icone
const I = {
  bus: '<svg viewBox="0 0 24 24"><rect x="5" y="3" width="14" height="15" rx="3"/><path d="M5 11h14M8 21v-3M16 21v-3"/><circle cx="9" cy="14.5" r=".7"/><circle cx="15" cy="14.5" r=".7"/></svg>',
  tram: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="13" rx="3"/><path d="M9 2h6M12 2v4M6 13h12M8 22l2-3M16 22l-2-3"/></svg>',
  metro: '<svg viewBox="0 0 24 24"><rect x="5" y="3" width="14" height="15" rx="4"/><path d="M5 11h14M9 21l1.5-3M15 21l-1.5-3"/><path d="M9 7h6"/></svg>',
  walk: '<svg viewBox="0 0 24 24"><circle cx="13" cy="4" r="2"/><path d="m9 21 2.5-6.5L14 17v4M7 12l2.5-4.5L14 8l2.5 4M11.5 14.5 13 9"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><path d="M12 22V10"/><rect x="6" y="2" width="12" height="8" rx="2"/><path d="M9 6h6"/></svg>',
  pin: '<svg viewBox="0 0 24 24"><path d="M12 22s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/></svg>',
  target: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="7.5"/></svg>',
  map: '<svg viewBox="0 0 24 24"><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z"/><path d="M9 4v14M15 6v14"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>',
  chev: '<svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>',
  swap: '<svg viewBox="0 0 24 24"><path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3"/></svg>',
  live: '<svg class="rt-ico" viewBox="0 0 24 24"><path d="M5 12.5a7 7 0 0 1 7-7M5 12.5M8.5 12.5a3.5 3.5 0 0 1 3.5-3.5"/><circle cx="12.5" cy="12" r="1.2" fill="currentColor"/></svg>',
  follow: '<svg viewBox="0 0 24 24"><path d="m3 11 18-8-8 18-2-8-8-2z"/></svg>',
  line: '<svg viewBox="0 0 24 24"><circle cx="5" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 6h6a4 4 0 0 1 0 8h-2a4 4 0 0 0 0 8"/></svg>',
  clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  alert: '<svg viewBox="0 0 24 24"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17v.5"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>',
  close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
};
const modeIcon = (t) => (t === 1 || t === 2 ? I.metro : t === 0 ? I.tram : I.bus);

// ------------------------------------------------------------------ linee
const R = (i) => S.routes[i];
const rColor = (r) => "#" + (r ? r.color : "6b7280");
const rLabel = (r) => (r ? (r.type === 1 ? r.short.replace(/^M/, "") : r.short) : "?");
function badge(i, cls = "") {
  const r = R(i);
  if (!r) return `<span class="badge ${cls}" style="background:#6b7280">?</span>`;
  return `<span class="badge ${r.type === 1 ? "metro" : ""} ${cls}" style="background:${rColor(r)};color:#${r.text || "fff"}">${esc(rLabel(r))}</span>`;
}
function badges(list, max = 14) {
  const extra = list.length > max ? `<span class="badge" style="background:var(--card-hover);color:var(--muted)">+${list.length - max}</span>` : "";
  return `<div class="badges">${list.slice(0, max).map((i) => badge(i)).join("")}${extra}</div>`;
}
function routeName(r, dirs) {
  if (r.long) return titleCase(r.long);
  if (dirs && dirs.length) return dirs.map((d) => titleCase(d.headsign)).join(" ⇄ ");
  return MODE_NAMES[r.type] + " " + r.short;
}

// ------------------------------------------------------------------ mappa
const map = L.map("map", { zoomControl: false, attributionControl: true, minZoom: 9, maxZoom: 19, worldCopyJump: false })
  .setView([41.8986, 12.4823], 13);
L.control.zoom({ position: "bottomright" }).addTo(map);
map.attributionControl.setPrefix(false);
// Tile Stadia: già nei colori del tema (niente filtri CSS costosi durante lo zoom), ma gratuite
// senza registrazione solo su localhost. Online servono una chiave o il dominio registrato su
// stadiamaps.com (configurati sul server); altrimenti OpenStreetMap con filtro scuro.
const LOCAL = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const TILES = {
  dark: "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png",
  light: "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}{r}.png",
  osm: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
};
let useStadia = LOCAL;
let stadiaKey = "";
const tileUrl = (t) => (useStadia ? TILES[t === "light" ? "light" : "dark"] + (stadiaKey ? "?api_key=" + encodeURIComponent(stadiaKey) : "") : TILES.osm);
let tileErrors = 0;
const tiles = L.tileLayer(tileUrl("dark"), {
  maxZoom: 19,
  // su telefono Leaflet carica le tile solo a dito fermo: durante il trascinamento comparivano zone nere
  updateWhenIdle: false,
  keepBuffer: 4,
  attribution: '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · Dati <a href="https://romamobilita.it/it/dati-aperti">Roma Mobilità</a>',
}).addTo(map);
tiles.getContainer().classList.toggle("base-tiles", !useStadia);
function setTileProvider(cfg) {
  const want = LOCAL || cfg.provider === "stadia";
  if (want === useStadia && (cfg.key || "") === stadiaKey) return;
  useStadia = want;
  stadiaKey = cfg.key || "";
  tiles.setUrl(tileUrl(document.documentElement.dataset.theme));
  tiles.getContainer().classList.toggle("base-tiles", !useStadia);
}
tiles.on("tileerror", () => {
  if (++tileErrors === 6 && useStadia) {      // Stadia rifiuta le richieste: si passa a OpenStreetMap
    useStadia = false;
    tiles.setUrl(TILES.osm);
    tiles.getContainer().classList.add("base-tiles");
  }
});

map.createPane("shapes").style.zIndex = 380;
map.createPane("stopsPane").style.zIndex = 420;
map.createPane("focusStops").style.zIndex = 450;
const focusCanvas = L.canvas({ pane: "focusStops", padding: 0.3 });
const metroLayer = L.layerGroup().addTo(map);
const focusLayer = L.layerGroup().addTo(map);
const planLayer = L.layerGroup().addTo(map);
let meMarker = null;

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  if (useStadia) tiles.setUrl(tileUrl(t));
  store("movite-theme", t);
  $('meta[name="theme-color"]').content = t === "light" ? "#eef1f6" : "#0b0f1a";
  if (S.stops) drawStops();   // al primo avvio le fermate non sono ancora caricate
}
setTheme(store("movite-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));

// ------------------------------------------------------------------ sidebar apribile
const panelOpen = () => isMobile() || !document.body.classList.contains("panel-closed");
function setPanel(open) {
  if (isMobile()) { setSheet(open ? "normal" : "collapsed"); return; }
  document.body.classList.toggle("panel-closed", !open);
  store("movite-panel", open ? "open" : "closed");
}
if (store("movite-panel") === "closed" && !isMobile()) document.body.classList.add("panel-closed");
$("#panel-close").addEventListener("click", () => setPanel(false));
$("#panel-open").addEventListener("click", () => setPanel(true));

function fitPadding() {
  if (isMobile()) return { paddingTopLeft: [20, 70], paddingBottomRight: [20, $("#panel").offsetHeight + 20] };
  return { paddingTopLeft: [panelOpen() ? 460 : 60, 40], paddingBottomRight: [80, 40] };
}
function fit(bounds, maxZoom = 16) {
  if (bounds && bounds.isValid()) map.flyToBounds(bounds, { ...fitPadding(), maxZoom, duration: 0.8 });
}
function panTo(latlng, zoom) {
  const z = zoom || Math.max(map.getZoom(), 15);
  // compensa lo spazio occupato dal pannello
  const p = map.project(latlng, z);
  if (isMobile()) p.y += $("#panel").offsetHeight / 2 - 20;
  else if (panelOpen()) p.x -= 215;
  map.flyTo(map.unproject(p, z), z, { duration: 0.8 });
}

// ------------------------------------------------------------------ fermate sulla mappa
// Le ~8000 fermate di superficie sono disegnate a mano su un canvas (visibili da zoom 15):
// come layer Leaflet, aggiungerle/toglierle bloccava la mappa per ~50 ms a ogni cambio di zoom.
// Canvas alla densità reale dello schermo: Leaflet usa sempre 2x, su iPhone (3x) le icone risultavano sfocate.
// Safari rifiuta canvas oltre ~16,7 milioni di pixel: in quel caso si scende di densità.
const SharpCanvas = L.Canvas.extend({
  _update() {
    if (this._map._animatingZoom && this._bounds) return;
    L.Renderer.prototype._update.call(this);
    const b = this._bounds, size = b.getSize(), c = this._container;
    let m = Math.min(3, window.devicePixelRatio || 1);
    while (m > 1 && size.x * size.y * m * m > 16e6) m -= 0.5;
    L.DomUtil.setPosition(c, b.min);
    c.width = Math.round(m * size.x);
    c.height = Math.round(m * size.y);
    c.style.width = size.x + "px";
    c.style.height = size.y + "px";
    this._ctx.setTransform(m, 0, 0, m, 0, 0);
    this._ctx.translate(-b.min.x, -b.min.y);
    // sistema di riferimento del canvas: durante zoom e spostamenti animati Leaflet lo trasforma via CSS,
    // quindi chi ridisegna nel frattempo deve usare questo, non lo zoom/origine correnti della mappa
    this._frame = { zoom: this._map.getZoom(), origin: this._map.getPixelOrigin() };
    this.fire("update");
  },
});
const stopRenderer = new SharpCanvas({ pane: "stopsPane", padding: 0.3 }).addTo(map);
let stopPts = [];   // [indice fermata, x, y] in coordinate layer, per l'hit-test

function drawStops() {
  const ctx = stopRenderer._ctx;
  if (!ctx || !stopRenderer._bounds) return;
  const cv = stopRenderer._container;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.restore();
  stopPts = [];
  const st = S.stops;
  if (!st || map.getZoom() < 15) return;
  // anelli neutri (gialli se servite da tram), per non confonderle con i mezzi colorati
  const light = document.documentElement.dataset.theme === "light";
  const ring = light ? "#64748b" : "#aab4c8";
  const geo = map.getBounds().pad(0.3);
  const s0 = geo.getSouth(), n0 = geo.getNorth(), w0 = geo.getWest(), e0 = geo.getEast();
  ctx.lineWidth = 2;
  ctx.fillStyle = light ? "#ffffff" : "#1a2133";
  for (const tram of [false, true]) {
    ctx.strokeStyle = tram ? "#f5a524" : ring;
    ctx.beginPath();
    for (let i = 0; i < st.ids.length; i++) {
      const mask = st.types[i];
      if (mask === 0 || mask === 2 || !!(mask & 1) !== tram) continue;   // 2 = solo metro: marker dedicati
      const lat = st.lat[i], lon = st.lon[i];
      if (lat < s0 || lat > n0 || lon < w0 || lon > e0) continue;
      const p = map.latLngToLayerPoint([lat, lon]);
      ctx.moveTo(p.x + 4, p.y);
      ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
      stopPts.push([i, p.x, p.y]);
    }
    ctx.fill();
    ctx.stroke();
  }
}
stopRenderer.on("update", drawStops);

function hitStop(layerPoint) {
  let best = null, bd = 8 * 8;
  for (const [i, x, y] of stopPts) {
    const d = (x - layerPoint.x) ** 2 + (y - layerPoint.y) ** 2;
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}

function buildStops() {
  const st = S.stops;
  drawStops();
  // stazioni della metropolitana, sempre visibili
  const seen = new Set();
  for (const [k, lines] of Object.entries(st.metro)) {
    const i = +k;
    const key = st.names[i].toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const r = R(lines[0]);
    const icon = L.divIcon({ className: "veh-wrap", iconSize: [16, 16], html: `<div class="metro-stop" style="--c:${rColor(r)}"></div>` });
    const m = L.marker([st.lat[i], st.lon[i]], { icon, pane: "stopsPane", zIndexOffset: -500, bubblingMouseEvents: true });
    m.bindTooltip(`${lines.map((l) => badge(l)).join(" ")} ${esc(titleCase(st.names[i]))}`, { className: "mv-tip", direction: "top", offset: [0, -8] });
    m.on("click", (e) => { if (!hitVehicle(map.mouseEventToLayerPoint(e.originalEvent))) openView({ type: "stop", i }); });
    metroLayer.addLayer(m);
  }
}

// ------------------------------------------------------------------ veicoli
// Tutti i mezzi sono disegnati su un unico canvas: con ~900 marker HTML lo zoom scattava.
map.createPane("vehiclesPane").style.zIndex = 610;
map.getPane("vehiclesPane").style.pointerEvents = "none";
const vehRenderer = new SharpCanvas({ pane: "vehiclesPane", padding: 0.4 }).addTo(map);
const hoverTip = L.tooltip({ className: "mv-tip", direction: "top", offset: [0, -18], opacity: 1 });
let drawn = [];          // veicoli disegnati nell'ultimo frame (per hit-test), dal basso verso l'alto
let needDraw = true;
let hovered = null;
const labelWidth = new Map();

function vType(d) { const r = R(d.r); return r ? (r.type === 2 || r.type === 7 ? 1 : r.type) : 3; }
function vVisible(v) {
  return S.filter[vType(v.d)] || S.selVehicle === v.d.id || (S.focusRoutes != null && S.focusRoutes.has(v.d.r));
}
function requestDraw() { needDraw = true; }
const applyVehicleFilter = requestDraw;
const refreshAllVehicleClasses = requestDraw;

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// pittogramma bianco del mezzo, disegnato in una griglia 24x24
function drawGlyph(ctx, type, color, label) {
  ctx.fillStyle = "#fff";
  if (type === 1 || type === 2) {
    ctx.font = `800 ${label.length > 1 ? 12 : 15}px "Plus Jakarta Sans", system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, 12, 13);
    return;
  }
  if (type === 0) {
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1.8;
    ctx.beginPath(); ctx.moveTo(8, 2.5); ctx.lineTo(16, 2.5); ctx.moveTo(12, 2.5); ctx.lineTo(12, 5.5); ctx.stroke();
    roundRect(ctx, 6, 5.5, 12, 15, 3); ctx.fill();
    ctx.fillStyle = color;
    roundRect(ctx, 8, 8.5, 8, 5, 1); ctx.fill();
    ctx.beginPath(); ctx.arc(9.5, 17, 1.2, 0, 7); ctx.arc(14.5, 17, 1.2, 0, 7); ctx.fill();
    return;
  }
  roundRect(ctx, 5, 3.5, 14, 15.5, 3); ctx.fill();
  ctx.fillRect(6.5, 18, 3, 3); ctx.fillRect(14.5, 18, 3, 3);
  ctx.fillStyle = color;
  roundRect(ctx, 7, 6.5, 10, 5.5, 1); ctx.fill();
  ctx.beginPath(); ctx.arc(8.8, 15.4, 1.2, 0, 7); ctx.arc(15.2, 15.4, 1.2, 0, 7); ctx.fill();
}

function drawVehicle(ctx, v, p, z, t) {
  const d = v.d;
  const r = R(d.r);
  const type = r ? r.type : 3;
  const color = rColor(r);
  const sel = S.selVehicle === d.id;
  const x = p.x, y = p.y;

  // mezzi di altre linee mentre se ne guarda una: puntini discreti, non sagome sbiadite
  if (v.rank === 0) {
    ctx.beginPath();
    ctx.arc(x, y, z < 13 ? 2.5 : 3.5, 0, 7);
    ctx.fillStyle = "rgba(139,147,168,.55)";
    ctx.fill();
    return;
  }
  const far = z < 13 && !sel;
  const s = sel ? 1.3 : far ? 0.5 : z < 14 ? 0.78 : 1;
  const Rr = 11 * s;

  if (sel) {   // anello pulsante sul mezzo selezionato
    const k = (t % 1600) / 1600;
    ctx.beginPath();
    ctx.arc(x, y, Rr + 4 + k * 16, 0, 7);
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.35 * (1 - k);
    ctx.fill();
    ctx.globalAlpha = 1;
  } else if (!far) {
    ctx.beginPath();
    ctx.arc(x, y, Rr + 4.5, 0, 7);
    if (d.est) {   // posizione stimata dall'orario: anello tratteggiato attorno, icona piena e leggibile
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = color;
      ctx.setLineDash([2.5, 2.5]);
      ctx.stroke();
      ctx.setLineDash([]);
    } else {       // alone: segnale GPS live
      ctx.fillStyle = color + "38";
      ctx.fill();
    }
  }

  // corpo a goccia orientato verso la direzione di marcia
  ctx.beginPath();
  if (d.b != null && !far) {
    const a = (d.b - 90) * Math.PI / 180;
    const phi = 0.94; // acos(1/1.7): tangenti dalla punta al cerchio
    ctx.moveTo(x + Math.cos(a) * Rr * 1.7, y + Math.sin(a) * Rr * 1.7);
    ctx.arc(x, y, Rr, a + phi, a - phi + Math.PI * 2);
    ctx.closePath();
  } else {
    ctx.arc(x, y, Rr, 0, 7);
  }
  if (!far) {   // ombra economica (shadowBlur su 900 forme sarebbe lento)
    ctx.save(); ctx.translate(0, 1.5 * s); ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fill(); ctx.restore();
  }
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = far ? 1.2 : 2 * s;
  ctx.strokeStyle = "#fff";
  ctx.stroke();
  if (far) return;

  const g = Rr * 1.2;
  ctx.save();
  ctx.translate(x - g / 2, y - g / 2);
  ctx.scale(g / 24, g / 24);
  drawGlyph(ctx, type, color, rLabel(r));
  ctx.restore();
}

// etichetta col numero di linea (non per la metro: la lettera è già nell'icona).
// Si salta se si sovrapporrebbe a un'etichetta già messa: prima il selezionato, poi GPS, poi stimati.
function drawLabels(ctx, list, z) {
  const placed = [];
  ctx.font = '800 11px "Plus Jakarta Sans", system-ui, sans-serif';
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (let k = list.length - 1; k >= 0; k--) {
    const v = list[k];
    const r = R(v.d.r);
    const sel = S.selVehicle === v.d.id;
    if (v.rank === 0 || (r && r.type === 1) || !(z >= 14 || sel)) continue;
    const label = rLabel(r);
    let w = labelWidth.get(label);
    if (w == null) { w = ctx.measureText(label).width; labelWidth.set(label, w); }
    const Rr = 11 * (sel ? 1.3 : z < 14 ? 0.78 : 1);
    const lw = w + 10, lh = 16;
    const lx = v.px.x + Rr * 0.55, ly = v.px.y - Rr - lh * 0.55;
    if (!sel && placed.some((q) => lx < q[2] + 2 && lx + lw > q[0] - 2 && ly < q[3] + 2 && ly + lh > q[1] - 2)) continue;
    placed.push([lx, ly, lx + lw, ly + lh]);
    const color = rColor(r);
    roundRect(ctx, lx, ly, lw, lh, 5);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "#fff";
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.fillText(label, lx + 5, ly + lh / 2 + 0.5);
  }
}

function drawVehicles(t) {
  const ctx = vehRenderer._ctx;
  if (!ctx || !vehRenderer._bounds) return;
  const cv = vehRenderer._container;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.restore();
  // coordinate nel riferimento del canvas (vedi SharpCanvas): le icone restano sul loro tracciato
  // anche mentre la mappa zooma o si sposta in modo animato
  const f = vehRenderer._frame;
  const z = f.zoom;
  const b = vehRenderer._bounds;
  const list = [];
  for (const v of S.vehicles.values()) {
    if (!vVisible(v)) continue;
    const p = map.project(v.pos, f.zoom).subtract(f.origin);
    if (!b.contains(p)) continue;
    v.px = p;
    const dim = S.focusRoutes != null && !S.focusRoutes.has(v.d.r) && S.selVehicle !== v.d.id;
    v.rank = S.selVehicle === v.d.id ? 4 : dim ? 0 : v.d.est ? 1 : 2;
    list.push(v);
  }
  list.sort((a, b2) => a.rank - b2.rank || a.px.y - b2.px.y);
  for (const v of list) drawVehicle(ctx, v, v.px, z, t);
  drawLabels(ctx, list, z);
  drawn = list;
}
vehRenderer.on("update", () => drawVehicles(performance.now()));

function hitVehicle(layerPoint) {
  const rad = map.getZoom() < 13 ? 9 : 17;
  for (let i = drawn.length - 1; i >= 0; i--) {
    const v = drawn[i];
    if (v.rank === 0) continue;   // i mezzi attenuati non sono cliccabili
    const p = map.latLngToLayerPoint(v.pos);     // posizione attuale, nel riferimento corrente della mappa
    const dx = p.x - layerPoint.x, dy = p.y - layerPoint.y;
    if (dx * dx + dy * dy <= rad * rad) return v;
  }
  return null;
}
map.on("mousemove", (e) => {
  const v = hitVehicle(e.layerPoint);
  // chiave dell'elemento sotto il mouse: un mezzo, oppure una fermata (numero)
  const h = v || hitStop(e.layerPoint);
  if (h === hovered) { if (v) hoverTip.setLatLng(v.pos); return; }
  hovered = h;
  map.getContainer().style.cursor = h != null ? "pointer" : "";
  if (v) hoverTip.setLatLng(v.pos).setContent(tipFor(v.d));
  else if (h != null) hoverTip.setLatLng([S.stops.lat[h], S.stops.lon[h]]).setContent(esc(titleCase(S.stops.names[h])));
  if (h != null) map.openTooltip(hoverTip); else map.closeTooltip(hoverTip);
});
map.on("mouseout", () => { hovered = null; map.closeTooltip(hoverTip); });

async function pollVehicles() {
  try {
    applyVehicles(await api(`/api/vehicles?bus_est=${S.busEst ? 1 : 0}`));
  } catch (e) { console.warn(e); }
}

// finché il feed GPS non è arrivato riprova ogni secondo, poi ogni 10
async function vehicleLoop(delay) {
  await new Promise((r) => setTimeout(r, delay));
  await pollVehicles();
  vehicleLoop(S.feedTs ? 10000 : 1000);
}

function applyVehicles(res) {
  if (!res) return;
  const firstGps = !S.feedTs && res.ts;
  S.feedTs = res.ts;
  {
    const seen = new Set();
    const t0 = performance.now();
    for (const d of res.vehicles) {
      seen.add(d.id);
      const v = S.vehicles.get(d.id);
      if (!v) {
        S.vehicles.set(d.id, { d, pos: L.latLng(d.lat, d.lon) });
        continue;
      }
      if (Math.abs(v.pos.lat - d.lat) + Math.abs(v.pos.lng - d.lon) > 1e-6) {
        v.from = v.pos; v.to = L.latLng(d.lat, d.lon); v.t0 = t0; S.moving.add(v);
      }
      v.d = d;
    }
    for (const [id, v] of S.vehicles) {
      if (!seen.has(id)) { S.vehicles.delete(id); S.moving.delete(v); }
    }
    requestDraw();
    onVehiclesUpdated();
    // all'arrivo dei primi dati GPS aggiorna i contatori della schermata iniziale
    if (firstGps && S.stack[S.stack.length - 1]?.type === "explore" && !$("#q")?.value) render(true);
  }
}

function tipFor(d) {
  const sp = d.s != null ? ` · ${d.s} km/h` : "";
  return `${badge(d.r)} → ${esc(titleCase(d.h || ""))}${sp}${d.est ? " · <i>stimato</i>" : ' · <span style="color:var(--good)">GPS</span>'}`;
}

function animate(t) {
  if (S.moving.size) needDraw = true;
  for (const v of S.moving) {
    let k = (t - v.t0) / 1800;
    if (k >= 1) { k = 1; S.moving.delete(v); }
    const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    v.pos = L.latLng(v.from.lat + (v.to.lat - v.from.lat) * e, v.from.lng + (v.to.lng - v.from.lng) * e);
  }
  const sel = S.selVehicle && S.vehicles.get(S.selVehicle);
  if (sel) needDraw = true;   // anello pulsante
  if (S.follow && sel && S.moving.has(sel)) map.panTo(sel.pos, { animate: false });
  if (needDraw) { needDraw = false; drawVehicles(t); }
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);

// ------------------------------------------------------------------ stato server
async function pollStatus() {
  try {
    const st = await api("/api/status");
    S.skew = st.server_time - Date.now() / 1000;
    S.feeds = st.feeds;
    S.walk = st.walk;
    if (st.tiles) setTileProvider(st.tiles);
    const pill = $("#live-pill");
    const live = st.counts ? st.counts.vehicles : 0;
    const age = st.feeds.vehicles ? now() - st.feeds.vehicles : null;
    pill.classList.toggle("on", !!live && age < 180);
    pill.classList.toggle("err", !!st.rt_error);
    pill.querySelector("span").textContent = st.rt_error ? "Feed non raggiungibile" :
      live ? `${live} mezzi GPS · ${ago(st.feeds.vehicles)}` : "In attesa del feed…";
    return st;
  } catch (e) {
    $("#live-pill").classList.add("err");
    $("#live-pill span").textContent = "Server non raggiungibile";
    return null;
  }
}

async function pollAlerts() {
  try {
    S.alerts = await api("/api/alerts");
    $("#alerts-count").textContent = S.alerts.length || "";
  } catch (e) { /* ignorato */ }
}

// ------------------------------------------------------------------ navigazione pannello
const view = $("#view");
view.addEventListener("click", (e) => onListClick(e));

function openView(v, replace = false) {
  if (replace) S.stack.pop();
  S.stack.push(v);
  if (isMobile() && $("#panel").classList.contains("collapsed")) setSheet("normal");
  if (!panelOpen()) setPanel(true);   // es. clic su un mezzo con la sidebar chiusa
  render();
}
function back() { if (S.stack.length > 1) { S.stack.pop(); render(); } }
// esce dalla visualizzazione del singolo mezzo, tornando alla vista da cui lo si era aperto
function closeVehicle() {
  while (S.stack.length > 1 && S.stack[S.stack.length - 1].type === "vehicle") S.stack.pop();
  if (S.stack[S.stack.length - 1].type === "vehicle") S.stack = [{ type: S.tab }];
  render();
}
function setTab(tab) {
  S.tab = tab;
  // su telefono, se il pannello è abbassato, toccare una scheda lo fa risalire per mostrarne il contenuto
  if (isMobile() && $("#panel").classList.contains("collapsed")) setSheet("normal");
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  S.stack = [{ type: tab }];
  render();
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

function clearFocus() {
  focusLayer.clearLayers();
  S.focusRoutes = null;
  S.selVehicle = null;
  S.follow = false;
  if (S.stack[S.stack.length - 1]?.type !== "plan") planLayer.clearLayers();
  refreshAllVehicleClasses();
  applyVehicleFilter();
}

let renderToken = 0;
async function render(refresh = false) {
  const v = S.stack[S.stack.length - 1];
  const token = ++renderToken;
  if (!refresh) { clearFocus(); view.scrollTop = 0; }
  renderViewBar(v);
  try {
    const html = await VIEWS[v.type](v, refresh);
    if (token !== renderToken || html == null) return;
    const scroll = view.scrollTop;
    view.innerHTML = `<div class="${refresh ? "" : "fade-in"}">${html}</div>`;
    if (refresh) view.scrollTop = scroll;
    BIND[v.type]?.(v, refresh);
  } catch (e) {
    if (token !== renderToken) return;
    view.innerHTML = `<div class="empty">${I.alert}<div>${esc(e.message)}</div></div>`;
  }
}

// barra fissa in cima alla sidebar: sul singolo mezzo la X per uscirne (fa da "indietro"),
// nelle altre schermate di dettaglio (fermata, linea) il pulsante Indietro
function renderViewBar(v) {
  const bar = $("#view-bar");
  const isVehicle = v.type === "vehicle";
  const hasBack = !isVehicle && S.stack.length > 1;
  bar.hidden = !isVehicle && !hasBack;
  bar.innerHTML = isVehicle
    ? `<button class="close-x" id="btn-close-veh" title="Esci dalla visualizzazione del mezzo (Esc)">${I.close}Chiudi mezzo</button>`
    : hasBack ? `<button class="back" id="btn-back" title="Indietro (Esc)">${I.back}Indietro</button>` : "";
  $("#btn-close-veh")?.addEventListener("click", closeVehicle);
  $("#btn-back")?.addEventListener("click", back);
}

function onVehiclesUpdated() {
  const v = S.stack[S.stack.length - 1];
  if (!v) return;
  if (v.type === "route" || v.type === "vehicle") render(true);
}

// ------------------------------------------------------------------ viste
const VIEWS = {};
const BIND = {};

// ---------- Esplora
VIEWS.explore = async (v) => {
  const live = [...S.vehicles.values()];
  const gps = live.filter((x) => !x.d.est).length;
  const counts = {};
  live.forEach((x) => { counts[x.d.r] = (counts[x.d.r] || 0) + 1; });
  const metro = S.routes.filter((r) => r.type === 1 && r.has_pattern && ["MA", "MB", "MC"].includes(r.short));
  const busy = Object.entries(counts).map(([i, n]) => [+i, n]).filter(([i]) => R(i) && R(i).type !== 1)
    .sort((a, b) => b[1] - a[1]).slice(0, 8);
  return `
    <div class="search">${I.search}<input id="q" placeholder="Cerca linea, fermata o codice fermata" autocomplete="off" value="${esc(v.q || "")}"></div>
    <div id="results"></div>
    <div id="home">
      <div class="stat-grid">
        <div class="stat"><b>${gps}</b><span>mezzi con GPS</span></div>
        <div class="stat"><b>${live.length - gps}</b><span>stimati da orario</span></div>
        <div class="stat"><b>${S.routes.length}</b><span>linee</span></div>
      </div>
      <div class="section-title">Metropolitana</div>
      <div class="metro-cards">
        ${metro.map((r) => `<div class="metro-card" data-route="${r.i}" style="--c:${rColor(r)}">${badge(r.i, "lg")}<small>${counts[r.i] || 0} treni in linea</small></div>`).join("")}
      </div>
      <div class="section-title">Vicino a te ${S.me ? "" : '<a id="near-btn">Usa posizione</a>'}</div>
      <div id="nearby" class="list">${S.me ? '<div class="hint">Cerco fermate vicine…</div>' : '<div class="hint">Attiva la posizione per vedere le fermate e i passaggi vicini.</div>'}</div>
      <div class="section-title">Linee più attive ora</div>
      <div class="list">
        ${busy.map(([i, n]) => `<button class="item" data-route="${i}">${badge(i)}<div class="grow"><div class="title">${esc(routeName(R(i)))}</div><div class="sub">${MODE_NAMES[R(i).type]} · ${esc(R(i).agency)}</div></div><div class="right sub">${n} in linea</div></button>`).join("")}
      </div>
    </div>`;
};
BIND.explore = (v) => {
  const q = $("#q");
  const run = debounce(async () => {
    v.q = q.value;
    const text = q.value.trim();
    $("#home").style.display = text ? "none" : "";
    if (!text) { $("#results").innerHTML = ""; return; }
    const res = await api(`/api/search?q=${encodeURIComponent(text)}`);
    if (q.value.trim() !== text) return;
    $("#results").innerHTML = (res.routes.length ? `<div class="section-title">Linee</div><div class="list">${res.routes.map((r) =>
      `<button class="item" data-route="${r.i}">${badge(r.i)}<div class="grow"><div class="title">${esc(routeName(r))}</div><div class="sub">${MODE_NAMES[r.type]} · ${esc(r.agency)}</div></div>${I.chev}</button>`).join("")}</div>` : "") +
      (res.stops.length ? `<div class="section-title">Fermate</div><div class="list">${res.stops.map(stopItem).join("")}</div>` : "") +
      (!res.routes.length && !res.stops.length ? `<div class="empty">${I.search}<div>Nessun risultato per “${esc(text)}”</div></div>` : "");
  }, 200);
  q.addEventListener("input", run);
  if (v.q) run();
  if (!isMobile()) q.focus();
  $("#near-btn")?.addEventListener("click", locate);
  if (S.me) loadNearby();
};
function stopItem(s) {
  return `<button class="item" data-stop="${s.i}"><div class="ico-circle">${I.stop}</div><div class="grow"><div class="title">${esc(titleCase(s.name))}</div><div class="sub">${s.dist != null ? fmtDist(s.dist) + " · " : ""}Fermata ${esc(s.code || s.id)}</div>${badges(s.routes, 10)}</div></button>`;
}
function onListClick(e) {
  const el = e.target.closest("[data-route],[data-stop],[data-vehicle]");
  if (!el || !view.contains(el)) return;
  if (el.dataset.route) openView({ type: "route", i: +el.dataset.route, dir: el.dataset.dir ? +el.dataset.dir : undefined });
  else if (el.dataset.stop) openView({ type: "stop", i: +el.dataset.stop });
  else if (el.dataset.vehicle) openView({ type: "vehicle", id: el.dataset.vehicle });
}
async function loadNearby() {
  const box = $("#nearby");
  if (!box || !S.me) return;
  const res = await api(`/api/nearby?lat=${S.me[0]}&lon=${S.me[1]}&r=600`);
  const seen = new Set();
  const list = res.filter((s) => { const k = s.name + s.routes.join(); if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 8);
  box.innerHTML = list.length ? list.map(stopItem).join("") : '<div class="hint">Nessuna fermata entro 600 m.</div>';
}

// ---------- Linee
VIEWS.lines = async (v) => {
  v.mode = v.mode ?? 1;
  const groups = { 1: "Metro", 0: "Tram", 3: "Bus", 9: "Altri operatori" };
  const list = S.routes.filter((r) => r.has_pattern && (v.mode === 9 ? r.agency !== "Atac" && r.type === 3 : v.mode === 3 ? r.type === 3 && r.agency === "Atac" : v.mode === 1 ? r.type === 1 || r.type === 2 : r.type === v.mode));
  list.sort((a, b) => sortKey(a.short).localeCompare(sortKey(b.short)));
  const counts = {};
  for (const x of S.vehicles.values()) counts[x.d.r] = (counts[x.d.r] || 0) + 1;
  return `
    <div class="seg" id="mode-seg">${Object.entries(groups).map(([k, n]) => `<button data-m="${k}" class="${+k === v.mode ? "on" : ""}">${n}</button>`).join("")}</div>
    <div class="search">${I.search}<input id="lf" placeholder="Filtra ${list.length} linee" value="${esc(v.f || "")}"></div>
    <div class="list" id="lines-list">
      ${list.map((r) => `<button class="item" data-route="${r.i}" data-k="${esc((r.short + " " + r.long).toLowerCase())}">${badge(r.i)}<div class="grow"><div class="title">${esc(routeName(r))}</div><div class="sub">${esc(r.agency)}</div></div><div class="right sub">${counts[r.i] ? `<span class="tag ok">${counts[r.i]} live</span>` : ""}</div></button>`).join("")}
    </div>`;
};
function sortKey(s) { const m = s.match(/\d+/); return (s[0] >= "0" && s[0] <= "9" ? "0" : "1") + (m ? m[0].padStart(4, "0") : "9999") + s; }
BIND.lines = (v) => {
  $$("#mode-seg button").forEach((b) => b.addEventListener("click", () => { v.mode = +b.dataset.m; v.f = ""; render(true); }));
  const f = $("#lf");
  const apply = () => { v.f = f.value; const q = f.value.toLowerCase().trim(); $$("#lines-list .item").forEach((el) => { el.style.display = !q || el.dataset.k.includes(q) ? "" : "none"; }); };
  f.addEventListener("input", apply);
  apply();
};

// ---------- Linea
VIEWS.route = async (v, refresh) => {
  if (!refresh || !v.data) v.data = await api(`/api/route?i=${v.i}`);
  else {
    // aggiorna solo i veicoli della linea a partire dal polling generale
    v.data.vehicles = [...S.vehicles.values()].filter((x) => x.d.r === v.i).map((x) => x.d);
  }
  const { route: r, directions: dirs, vehicles, alerts } = v.data;
  if (v.dir == null || v.dir >= dirs.length) v.dir = 0;
  const d = dirs[v.dir];
  S.focusRoutes = new Set([v.i]);
  const place = placeVehicles(dirs, vehicles);
  const live = vehicles.filter((x) => !x.est).length;
  return `
    <div class="hero">${badge(r.i, "lg")}<div><h2>${esc(routeName(r, dirs))}</h2>
      <div class="sub">${MODE_NAMES[r.type]} · ${esc(r.agency)} · ${live ? `<b style="color:var(--good)">${live} con GPS</b>` : ""}${vehicles.length - live ? ` ${vehicles.length - live} stimati` : live ? "" : "nessun mezzo in linea"}</div></div></div>
    ${alerts.length ? `<div class="alert" style="margin-bottom:10px"><h4>${I.alert} ${alerts.length} avvis${alerts.length > 1 ? "i" : "o"} su questa linea</h4><p>${esc(alerts[0].header)}</p></div>` : ""}
    ${dirs.length > 1 ? `<div class="seg" id="dir-seg">${dirs.map((x, k) => `<button data-d="${k}" class="${k === v.dir ? "on" : ""}">→ ${esc(titleCase(x.headsign))}</button>`).join("")}</div>` : ""}
    ${d ? `<div class="section-title">${d.stops.length} fermate</div>
    <div class="timeline" style="--c:${rColor(r)}">
      ${d.stops.map((s, k) => `
        <div class="tl-row ${k === 0 || k === d.stops.length - 1 ? "term" : ""}" data-stop="${s.i}">
          <div class="tl-rail"><i></i></div>
          <div class="tl-body"><div class="grow"><div class="tl-name">${esc(titleCase(s.name))}</div>
            ${(place[v.dir][k] || []).map((x) => `<div class="tl-veh ${x.est ? "est" : ""}" data-vehicle="${esc(x.id)}"><span class="vdot">${modeIcon(r.type)}</span>${x.est ? "Stimato" : "Vettura " + esc(x.l || x.id)}${x.s != null ? ` · ${x.s} km/h` : ""}</div>`).join("")}
            ${s.routes.length > 1 ? `<div class="tl-sub">${badges(s.routes.filter((q) => q !== r.i), 8)}</div>` : ""}</div>
          </div>
        </div>`).join("")}
    </div>` : '<div class="empty">Percorso non disponibile</div>'}`;
};
BIND.route = (v, refresh) => {
  $$("#dir-seg button").forEach((b) => b.addEventListener("click", () => { v.dir = +b.dataset.d; v.drawn = false; render(true); }));
  refreshAllVehicleClasses();
  applyVehicleFilter();
  if (v.drawn) return;
  v.drawn = true;
  focusLayer.clearLayers();
  const { route: r, directions: dirs } = v.data;
  const c = rColor(r);
  dirs.forEach((d, k) => {
    if (k === v.dir) return;
    L.polyline(d.shape, { pane: "shapes", color: c, weight: 4, opacity: 0.25 }).addTo(focusLayer);
  });
  const d = dirs[v.dir];
  if (!d) return;
  L.polyline(d.shape, { pane: "shapes", color: "#fff", weight: 9, opacity: 0.9 }).addTo(focusLayer);
  const line = L.polyline(d.shape, { pane: "shapes", color: c, weight: 6 }).addTo(focusLayer);
  d.stops.forEach((s, k) => {
    const term = k === 0 || k === d.stops.length - 1;
    L.circleMarker([s.lat, s.lon], { renderer: focusCanvas, radius: term ? 7 : 5, color: c, weight: 3, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(esc(titleCase(s.name)), { className: "mv-tip", direction: "top", offset: [0, -6] })
      .on("click", () => openView({ type: "stop", i: s.i })).addTo(focusLayer);
  });
  // il clic su una fermata della linea non deve arrivare anche alla mappa (che chiuderebbe la vista)
  focusLayer.eachLayer((l) => { l.options.bubblingMouseEvents = false; });
  if (!refresh || !v.fitted) { fit(line.getBounds(), 15); v.fitted = true; }
};

function placeVehicles(dirs, vehicles) {
  const out = dirs.map(() => ({}));
  for (const x of vehicles) {
    let best = null;
    dirs.forEach((d, k) => {
      const sameHead = x.h && d.headsign && x.h.toLowerCase() === d.headsign.toLowerCase();
      d.stops.forEach((s, j) => {
        const dist = Math.hypot(s.lat - x.lat, (s.lon - x.lon) * 0.75) - (sameHead ? 0.002 : 0);
        if (!best || dist < best.dist) best = { k, j, dist };
      });
    });
    if (best) (out[best.k][best.j] = out[best.k][best.j] || []).push(x);
  }
  return out;
}

// ---------- Fermata
VIEWS.stop = async (v, refresh) => {
  v.data = await api(`/api/stop?i=${v.i}`);
  const { stop: s, departures: deps, alerts } = v.data;
  const groups = new Map();
  for (const d of deps) {
    const k = d.route + "|" + d.headsign;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(d);
  }
  const rows = [...groups.values()].sort((a, b) => a[0].time - b[0].time);
  return `
    <div class="hero"><div class="ico-circle" style="width:46px;height:46px">${I.stop}</div><div><h2>${esc(titleCase(s.name))}</h2>
      <div class="sub">Fermata ${esc(s.code || s.id)}</div></div></div>
    ${badges(s.routes, 30)}
    <div class="chip-row" style="margin-top:12px">
      <button class="btn sm ghost" id="from-here">${I.pin} Parti da qui</button>
      <button class="btn sm ghost" id="to-here">${I.target} Arriva qui</button>
    </div>
    ${alerts.map((a) => `<div class="alert" style="margin-top:12px"><h4>${esc(a.header)}</h4><p>${esc(a.description)}</p></div>`).join("")}
    <div class="section-title">Prossimi passaggi <span style="text-transform:none;letter-spacing:0;font-weight:500">aggiornati ${fmtTime(now())}</span></div>
    <div class="list">
      ${rows.length ? rows.map((g) => depRow(g)).join("") : `<div class="empty">${I.clock}<div>Nessun passaggio nelle prossime 2 ore</div></div>`}
    </div>`;
};
function depRow(g) {
  const d = g[0];
  const m = mins(d.time);
  const next = g.slice(1, 4).map((x) => `${mins(x.time) <= 0 ? "ora" : mins(x.time) + "′"}${x.realtime ? "•" : ""}`).join("  ");
  const target = d.vehicle ? `data-vehicle="${esc(d.vehicle)}"` : `data-vehicle="est-${d.inst}"`;
  return `<div class="dep" ${target}>${badge(d.route)}
    <div class="grow"><div class="head">${esc(titleCase(d.headsign))}</div>
      <div class="more">${delayTag(d)} ${next ? "poi " + next : ""}</div></div>
    <div class="eta ${m <= 1 ? "now" : ""}"><b>${d.realtime ? I.live : ""}${m <= 0 ? "ora" : m + "<small>min</small>"}</b><div class="when">${fmtTime(d.time)}</div></div></div>`;
}
function delayTag(d) {
  if (!d.realtime) return '<span class="tag sched">programmato</span>';
  const m = Math.round(d.delay / 60);
  if (m >= 2) return `<span class="tag late">+${m} min</span>`;
  if (m <= -2) return `<span class="tag early">${m} min</span>`;
  return '<span class="tag ok">in orario</span>';
}
BIND.stop = (v, refresh) => {
  const s = v.data.stop;
  $("#from-here").onclick = () => { S.plan.from = { name: titleCase(s.name), lat: s.lat, lon: s.lon }; setTab("plan"); };
  $("#to-here").onclick = () => { S.plan.to = { name: titleCase(s.name), lat: s.lat, lon: s.lon }; setTab("plan"); };
  if (!refresh) {
    focusLayer.clearLayers();
    L.circleMarker([s.lat, s.lon], { renderer: focusCanvas, radius: 11, color: "#fff", weight: 4, fillColor: "#19d3a2", fillOpacity: 1 }).addTo(focusLayer);
    panTo([s.lat, s.lon], Math.max(map.getZoom(), 16));
  }
  clearInterval(v.timer);
  v.timer = setInterval(() => { if (S.stack[S.stack.length - 1] === v) render(true); else clearInterval(v.timer); }, 20000);
};

// ---------- Veicolo
VIEWS.vehicle = async (v, refresh) => {
  try { v.data = await api(`/api/vehicle?id=${encodeURIComponent(v.id)}`); }
  catch (e) { if (refresh && v.data) return null; throw e; }
  const d = v.data;
  const r = R(d.route);
  const type = r ? r.type : 3;
  S.selVehicle = d.id;
  S.focusRoutes = new Set([d.route]);
  const speed = d.speed == null ? null : Math.round(d.speed);
  const max = type === 1 ? 90 : 70;
  const frac = speed == null ? 0 : Math.min(1, speed / max);
  const src = d.estimated ? "stimata da orario" : d.speed_src === "gps" ? "GPS" : d.speed_src === "calcolata" ? "calcolata da GPS" : "in attesa di dati";
  const cur = d.stops && d.stops[Math.min(d.current, d.stops.length - 1)];
  const delay = d.realtime && d.delay != null ? Math.round(d.delay / 60) : null;
  return `
    <div class="hero">${badge(d.route, "lg")}<div><h2>→ ${esc(titleCase(d.headsign || ""))}</h2>
      <div class="sub">${MODE_NAMES[type]}${d.label ? " · Vettura " + esc(d.label) : ""}${d.estimated ? " · senza GPS" : ""}</div></div></div>
    <div class="veh-card">
      <div class="gauge">
        <svg viewBox="0 0 150 96"><path d="M15 85 A60 60 0 0 1 135 85" stroke="var(--card-hover)"/>
          <path d="M15 85 A60 60 0 0 1 135 85" stroke="${rColor(r)}" stroke-dasharray="${(frac * 188.5).toFixed(1)} 400"/></svg>
        <div class="val"><b>${speed == null ? "—" : speed}</b><span>km/h</span><span class="src">${src}</span></div>
      </div>
      <div class="kv">
        ${cur ? `<div><span>${d.estimated ? "Prossima" : esc(d.status || "Prossima")}</span><b>${esc(titleCase(cur.name))}</b></div>` : ""}
        <div><span>Puntualità</span><b>${delay == null ? '<span class="tag sched">nessun dato</span>' : delay >= 2 ? `<span class="tag late">+${delay} min</span>` : delay <= -2 ? `<span class="tag early">${delay} min</span>` : '<span class="tag ok">in orario</span>'}</b></div>
        ${d.occupancy ? `<div><span>Affollamento</span><b>${esc(d.occupancy)}</b></div>` : ""}
        <div><span>Aggiornato</span><b>${d.estimated ? "da orario" : ago(d.ts)}</b></div>
        ${d.odometer ? `<div><span>Percorsi</span><b>${fmtDist(d.odometer)}</b></div>` : ""}
      </div>
    </div>
    ${sparkline(d.speed_history, rColor(r))}
    <div class="chip-row" style="margin-top:10px">
      ${d.lat != null ? `<button class="btn sm ${S.follow ? "follow-on" : "ghost"}" id="follow">${I.follow} ${S.follow ? "Stai seguendo" : "Segui sulla mappa"}</button>` : ""}
      <button class="btn sm ghost" data-route="${d.route}">${I.line} Tutta la linea</button>
    </div>
    ${d.stops ? `<div class="section-title">Corsa ${d.realtime ? '<span class="tag ok" style="text-transform:none;letter-spacing:0">orari in tempo reale</span>' : '<span class="tag sched" style="text-transform:none;letter-spacing:0">orari programmati</span>'}</div>
    <div class="timeline" style="--c:${rColor(r)}">
      ${d.stops.map((s, k) => `
        <div class="tl-row ${k === 0 || k === d.stops.length - 1 ? "term" : ""} ${s.passed ? "passed" : ""} ${k === d.current ? "current" : ""}" data-stop="${s.i}" ${k === d.current ? 'id="cur-stop"' : ""}>
          <div class="tl-rail"><i></i></div>
          <div class="tl-body"><div class="grow"><div class="tl-name">${esc(titleCase(s.name))}</div></div>
            <div class="tl-time">${!s.passed && Math.abs(s.time - s.sched) >= 60 ? `<s>${fmtTime(s.sched)}</s>` : ""}${fmtTime(s.time)}${!s.passed && k >= d.current ? `<div>${mins(s.time) <= 0 ? "ora" : mins(s.time) + " min"}</div>` : ""}</div></div>
        </div>`).join("")}
    </div>` : ""}`;
};
function sparkline(hist, color) {
  if (!hist || hist.length < 2) return "";
  const W = 300, H = 60;
  const t0 = hist[0][0], t1 = hist[hist.length - 1][0];
  const vmax = Math.max(40, ...hist.map((h) => h[1]));
  const pts = hist.map(([t, s]) => [((t - t0) / Math.max(1, t1 - t0)) * W, H - 4 - (s / vmax) * (H - 10)]);
  const line = pts.map((p) => p.map((x) => x.toFixed(1)).join(",")).join(" ");
  const avg = Math.round(hist.reduce((a, h) => a + h[1], 0) / hist.length);
  return `<div class="spark"><div class="spark-head"><span>Velocità ultimi ${Math.max(1, Math.round((t1 - t0) / 60))} min</span><span>media ${avg} km/h · max ${Math.round(Math.max(...hist.map((h) => h[1])))}</span></div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <polygon points="0,${H} ${line} ${W},${H}" fill="url(#sg)" stroke="none"/><polyline points="${line}" stroke="${color}"/></svg></div>`;
}
BIND.vehicle = (v, refresh) => {
  const d = v.data;
  $("#follow")?.addEventListener("click", () => {
    S.follow = !S.follow;
    if (S.follow) { const m = S.vehicles.get(d.id); if (m) panTo(m.pos, Math.max(map.getZoom(), 16)); }
    render(true);
  });
  refreshAllVehicleClasses();
  applyVehicleFilter();
  // traccia percorsa + tracciato della corsa
  focusLayer.clearLayers();
  const c = rColor(R(d.route));
  if (d.shape) L.polyline(d.shape, { pane: "shapes", color: c, weight: 5, opacity: 0.45 }).addTo(focusLayer);
  if (d.history && d.history.length > 1) {
    L.polyline(d.history, { pane: "shapes", color: "#fff", weight: 7, opacity: 0.8 }).addTo(focusLayer);
    L.polyline(d.history, { pane: "shapes", color: c, weight: 4 }).addTo(focusLayer);
  }
  if (d.stops) d.stops.forEach((s) => {
    if (!s.passed) L.circleMarker([s.lat, s.lon], { renderer: focusCanvas, radius: 4, color: c, weight: 2.5, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(`${esc(titleCase(s.name))} · ${fmtTime(s.time)}`, { className: "mv-tip", direction: "top" }).addTo(focusLayer);
  });
  if (!refresh) {
    if (d.lat != null) panTo([d.lat, d.lon], Math.max(map.getZoom(), 15));
    setTimeout(() => $("#cur-stop")?.scrollIntoView({ block: "center", behavior: "smooth" }), 300);
  }
};

// ---------- Avvisi
VIEWS.alerts = async () => {
  await pollAlerts();
  if (!S.alerts.length) return `<div class="empty">${I.alert}<div>Nessun avviso attivo</div></div>`;
  const fmtD = (t) => new Date(t * 1000).toLocaleDateString("it-IT", { day: "numeric", month: "short", timeZone: "Europe/Rome" });
  return `<div class="section-title">${S.alerts.length} avvisi di servizio</div>` + S.alerts.map((a) => `
    <div class="alert"><h4>${esc(a.header)}</h4>${a.description && a.description !== a.header ? `<p>${esc(a.description)}</p>` : ""}
      ${a.routes.length ? badges(a.routes, 20) : ""}
      <div class="when">${a.start ? "dal " + fmtD(a.start) : ""}${a.end ? " al " + fmtD(a.end) : ""}</div></div>`).join("");
};
BIND.alerts = () => {
  $$(".alert").forEach((el) => el.addEventListener("click", () => el.classList.toggle("open")));
};

// ---------- Percorso
VIEWS.plan = async (v, refresh) => {
  const P = S.plan;
  const val = (p) => (p ? esc(p.name) : "");
  const whenVal = P.when ? new Date(P.when * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "";
  return `
    <div class="plan-box">
      <div class="plan-field from ${P.picking === "from" ? "picking" : ""}"><span class="pin"></span>
        <input id="pf" placeholder="Partenza: indirizzo, fermata o luogo" value="${val(P.from)}" autocomplete="off">
        <button class="mini" id="pf-map" title="Scegli sulla mappa">${I.map}</button>
        <div class="suggest" id="sg-from" hidden></div></div>
      <div class="plan-field to ${P.picking === "to" ? "picking" : ""}"><span class="pin"></span>
        <input id="pt" placeholder="Destinazione" value="${val(P.to)}" autocomplete="off">
        <button class="mini" id="pt-map" title="Scegli sulla mappa">${I.map}</button>
        <div class="suggest" id="sg-to" hidden></div></div>
      <button class="swap" id="swap" title="Inverti">${I.swap}</button>
    </div>
    ${P.picking ? `<div class="hint">Tocca un punto sulla mappa per impostare ${P.picking === "from" ? "la partenza" : "la destinazione"}</div>` : ""}
    <div class="plan-opts">
      <div class="seg" style="margin:0;flex:1 1 100%" id="when-seg">
        <button data-w="now" class="${P.when ? "" : "on"}">Parti ora</button>
        <button data-w="at" class="${P.when && !P.arrive ? "on" : ""}">Parti alle…</button>
        <button data-w="by" class="${P.when && P.arrive ? "on" : ""}">Arriva entro…</button></div>
      ${P.when ? `<input type="datetime-local" id="when" value="${whenVal}">` : ""}
    </div>
    <div class="chip-row" id="plan-modes">
      ${[[1, "Metro", "metro"], [0, "Tram", "tram"], [3, "Bus", "bus"], [2, "Treno", "rail"]].map(([m, n, c]) => `<button class="chip ${P.modes[m] ? "on" : ""}" data-m="${m}"><i class="dot ${c}"></i>${n}</button>`).join("")}
    </div>
    <div class="chip-row" id="plan-prefs" style="margin-top:6px">
      <button class="chip ${P.rail ? "on" : ""}" data-p="rail">${I.metro}Preferisci metro e tram</button>
      <button class="chip ${P.gps ? "on" : ""}" data-p="gps">${I.target}Evita bus senza GPS</button>
    </div>
    <div id="plan-results">${planResults()}</div>`;
};
function planResults() {
  const P = S.plan;
  if (P.loading) return `<div class="empty"><div class="loader" style="width:140px"><span></span></div><div style="margin-top:12px">Calcolo con gli arrivi in tempo reale…</div></div>`;
  if (P.error) return `<div class="empty">${I.alert}<div>${esc(P.error)}</div></div>`;
  if (!P.results) return `<div class="empty" style="padding-top:36px">${I.line}<div>Scegli partenza e destinazione.<br><span style="font-size:12.5px">Suggerimento: clic destro (o pressione lunga) sulla mappa per impostarle al volo.</span></div></div>`;
  if (!P.results.length) return `<div class="empty">${I.alert}<div>Nessun percorso trovato nelle prossime ore.</div></div>`;
  const hint = P.walkNet === false
    ? `<div class="walk-hint">${I.walk}<span>Tratti a piedi stimati in linea d'aria${S.walk?.disabled ? "." :
      `: la rete stradale si sta preparando${S.walk?.message ? ` (${esc(S.walk.message.replace(/^Rete pedonale: /, ""))})` : ""}.`}</span></div>` : "";
  const title = P.arrive && P.when ? `Arrivo entro le ${fmtTime(P.when)}` : `${P.results.length} soluzioni`;
  return `${hint}<div class="section-title">${title} <a id="replan">${I.refresh} Aggiorna</a></div>` + P.results.map((j, k) => journeyCard(j, k)).join("");
}
function journeyCard(j, k) {
  const sel = S.plan.sel === k;
  const legs = j.legs.map((l) => l.type === "walk"
    ? `<span class="walk">${I.walk}${Math.max(1, Math.round((l.arr - l.dep) / 60))}′</span>`
    : badge(l.route, l.nogps ? "nogps" : "")).join('<span class="sep">›</span>');
  const firstTransit = j.legs.find((l) => l.type === "transit");
  const leave = mins(j.dep);
  const lead = j.tags.includes("Consigliato") ? "Consigliato" : "Più veloce";
  const tags = j.tags.map((t) => `<span class="tag ${t === lead ? "fast" : "prof"}">${esc(t)}</span>`).join("");
  return `<div class="journey ${sel ? "sel" : ""}" data-j="${k}">
    <div class="j-tags">${tags}${j.realtime ? `<span class="tag ok">${I.live} tempo reale</span>` : ""}</div>
    <div class="j-top"><div><div class="j-dur">${fmtDur(j.duration)}</div><div class="j-times">${fmtTime(j.dep)} → ${fmtTime(j.arr)}</div></div></div>
    <div class="j-legs">${legs}</div>
    <div class="j-meta">${firstTransit ? `<span class="j-leave">${leave <= 0 ? "Parti subito" : "Parti tra " + leave + " min"}</span>` : "<span>Solo a piedi</span>"}
      ${firstTransit ? `<span>${j.transfers ? j.transfers + (j.transfers > 1 ? " cambi" : " cambio") : "Nessun cambio"}</span>` : ""}<span>${fmtDist(j.walk)} a piedi</span></div>
    ${sel ? journeySteps(j) : ""}
  </div>`;
}
function journeySteps(j) {
  const out = [];
  for (const l of j.legs) {
    if (l.type === "walk") {
      out.push(`<div class="step walk"><div class="t">${fmtTime(l.dep)}</div><div class="rail"><i></i></div><div class="body">${I.walk} Cammina <b>${fmtDist(l.distance)}</b> (${Math.max(1, Math.round((l.arr - l.dep) / 60))} min) fino a <b>${esc(titleCase(l.to.name))}</b></div></div>`);
    } else {
      const late = l.realtime && Math.abs(l.dep - l.sched_dep) >= 60;
      out.push(`<div class="step" style="--c:#${l.color}"><div class="t">${late ? `<s style="color:var(--faint)">${fmtTime(l.sched_dep)}</s><br>` : ""}${fmtTime(l.dep)}</div><div class="rail"><i></i></div><div class="body">
        Prendi ${badge(l.route)} <b>→ ${esc(titleCase(l.headsign))}</b><div class="muted">da ${esc(titleCase(l.from.name))} · ${delayTag(l)}${l.nogps ? ' <span class="tag early">in viaggio senza GPS</span>' : ""}</div>
        <div class="muted">Sii in ${l.mode === 1 || l.mode === 2 ? "banchina" : "fermata"} entro le <b style="color:var(--text)">${fmtTime(l.be_at)}</b></div>
        ${l.vehicle ? `<button class="btn sm ghost" style="margin-top:8px" data-vehicle="${esc(l.vehicle)}">${I.follow} Dov'è ora il mezzo</button>` : ""}
        <details><summary>${l.stops.length - 1} fermate · ${Math.round((l.arr - l.dep) / 60)} min</summary>${l.stops.slice(1).map((s) => `<div>${fmtTime(s.time)} · ${esc(titleCase(s.name))}</div>`).join("")}</details>
        <div class="muted">Scendi a <b style="color:var(--text)">${esc(titleCase(l.to.name))}</b> alle ${fmtTime(l.arr)}</div></div></div>`);
    }
  }
  out.push(`<div class="step walk"><div class="t">${fmtTime(j.arr)}</div><div class="rail"><i style="border-color:var(--bad)"></i></div><div class="body"><b>Arrivo</b> · ${esc(S.plan.to?.name || "")}</div></div>`);
  return `<div class="steps">${out.join("")}</div>`;
}
BIND.plan = (v) => {
  const P = S.plan;
  setupAuto($("#pf"), $("#sg-from"), "from");
  setupAuto($("#pt"), $("#sg-to"), "to");
  $("#pf-map").onclick = () => { P.picking = P.picking === "from" ? null : "from"; render(true); };
  $("#pt-map").onclick = () => { P.picking = P.picking === "to" ? null : "to"; render(true); };
  $("#swap").onclick = () => { [P.from, P.to] = [P.to, P.from]; drawPlanPins(); render(true); runPlan(); };
  $$("#when-seg button").forEach((b) => b.onclick = () => {
    const w = b.dataset.w;
    if (w === "now") { P.when = null; P.arrive = false; }
    else {
      // orario proposto: tra 10 minuti per partire, tra 45 per arrivare (arrotondati ai 5 minuti)
      if (!P.when) P.when = Math.round(now() / 300) * 300 + (w === "by" ? 2700 : 600);
      P.arrive = w === "by";
    }
    render(true); runPlan();
  });
  $$("#plan-prefs .chip").forEach((b) => b.onclick = () => {
    const k = b.dataset.p;
    P[k] = !P[k];
    store("movite-" + k, P[k] ? "1" : "0");
    render(true); runPlan();
  });
  $("#when")?.addEventListener("change", (e) => { P.when = new Date(e.target.value).getTime() / 1000; runPlan(); });
  $$("#plan-modes .chip").forEach((b) => b.onclick = () => { P.modes[b.dataset.m] = !P.modes[b.dataset.m]; render(true); runPlan(); });
  bindResults();
  drawPlanPins();
  if (P.results && P.results[P.sel]) drawJourney(P.results[P.sel], false);
};
function bindResults() {
  const box = $("#plan-results");
  if (!box) return;
  box.onclick = (e) => {
    if (e.target.closest("#replan")) { runPlan(); return; }
    if (e.target.closest("[data-vehicle]")) return; // gestito dal click globale del pannello
    if (e.target.closest("details")) return;
    const card = e.target.closest(".journey");
    if (!card) return;
    S.plan.sel = +card.dataset.j;
    box.innerHTML = planResults();
    drawJourney(S.plan.results[S.plan.sel], true);
  };
}

function setupAuto(input, box, which) {
  let items = [];
  let idx = -1;
  const choose = (it) => {
    if (it.me) { useMyLocation(which); return; }
    S.plan[which] = { name: it.name, lat: it.lat, lon: it.lon };
    input.value = it.name;
    box.hidden = true;
    drawPlanPins();
    runPlan();
  };
  const show = () => {
    box.innerHTML = items.map((it, k) => `<button class="item ${k === idx ? "sel" : ""}" data-k="${k}"><div class="ico-circle">${it.me ? I.target : it.stop ? I.stop : I.pin}</div><div class="grow"><div class="title">${esc(it.name)}</div><div class="sub">${esc(it.sub || "")}</div>${it.routes ? badges(it.routes, 8) : ""}</div></button>`).join("");
    box.hidden = !items.length;
  };
  const search = debounce(async () => {
    const q = input.value.trim();
    const base = which === "from" || !S.plan.from ? [{ me: true, name: "La mia posizione", sub: "Usa il GPS del dispositivo" }] : [];
    if (q.length < 2) { items = base; idx = -1; show(); return; }
    const [st, geo] = await Promise.all([api(`/api/search?q=${encodeURIComponent(q)}`).catch(() => ({ stops: [] })),
      q.length >= 3 ? api(`/api/geocode?q=${encodeURIComponent(q)}`).catch(() => []) : []]);
    if (input.value.trim() !== q) return;
    items = [...base,
      ...st.stops.slice(0, 5).map((s) => ({ name: titleCase(s.name), sub: "Fermata " + (s.code || s.id), lat: s.lat, lon: s.lon, stop: true, routes: s.routes })),
      ...geo.slice(0, 5).map((g) => ({ name: g.name, sub: g.full, lat: g.lat, lon: g.lon }))];
    idx = -1;
    show();
  }, 250);
  input.addEventListener("input", search);
  input.addEventListener("focus", () => { input.select(); search(); });
  input.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 180));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { idx = Math.min(items.length - 1, idx + 1); show(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { idx = Math.max(0, idx - 1); show(); e.preventDefault(); }
    else if (e.key === "Enter" && items.length) { choose(items[Math.max(0, idx)]); }
    else if (e.key === "Escape") box.hidden = true;
  });
  box.addEventListener("mousedown", (e) => {
    const b = e.target.closest("[data-k]");
    if (b) { e.preventDefault(); choose(items[+b.dataset.k]); }
  });
}

function useMyLocation(which) {
  getPosition().then((p) => {
    S.plan[which] = { name: "La mia posizione", lat: p[0], lon: p[1] };
    if (S.tab === "plan") { render(true); runPlan(); }
  });
}

let planSeq = 0;
async function runPlan() {
  const P = S.plan;
  if (!P.from || !P.to) return;
  const seq = ++planSeq;
  P.loading = true; P.error = null;
  const box = $("#plan-results");
  if (box) box.innerHTML = planResults();
  const modes = Object.entries(P.modes).filter(([, on]) => on).map(([m]) => m).join(",");
  try {
    const res = await api(`/api/plan?from=${P.from.lat},${P.from.lon}&to=${P.to.lat},${P.to.lon}&time=${P.when || ""}` +
      `&modes=${modes}&arrive=${P.arrive && P.when ? 1 : 0}&rail=${P.rail ? 1 : 0}&gps=${P.gps ? 1 : 0}`);
    if (seq !== planSeq) return;
    P.results = res.journeys;
    P.walkNet = res.walk_network;
    P.sel = 0;   // la prima è la consigliata (o la più veloce)
  } catch (e) {
    if (seq !== planSeq) return;
    P.error = e.message; P.results = null;
  }
  P.loading = false;
  const b2 = $("#plan-results");
  if (b2 && S.stack[S.stack.length - 1]?.type === "plan") {
    b2.innerHTML = planResults();
    bindResults();
    if (P.results && P.results[P.sel]) drawJourney(P.results[P.sel], true);
  }
}

function pinIcon(cls) { return L.divIcon({ className: "veh-wrap", iconSize: [26, 26], iconAnchor: [13, 26], html: `<div class="pin-mark ${cls}"></div>` }); }
function drawPlanPins() {
  planLayer.clearLayers();
  const P = S.plan;
  if (P.from) L.marker([P.from.lat, P.from.lon], { icon: pinIcon("from"), zIndexOffset: 2000 }).addTo(planLayer);
  if (P.to) L.marker([P.to.lat, P.to.lon], { icon: pinIcon("to"), zIndexOffset: 2000 }).addTo(planLayer);
}
function drawJourney(j, doFit) {
  drawPlanPins();
  S.focusRoutes = new Set(j.legs.filter((l) => l.type === "transit").map((l) => l.route));
  refreshAllVehicleClasses();
  applyVehicleFilter();
  const all = [];
  for (const l of j.legs) {
    if (l.type === "walk") {
      L.polyline(l.path, { pane: "shapes", color: "#8b93a8", weight: 4, dashArray: "2 8", lineCap: "round" }).addTo(planLayer);
      all.push(...l.path);
    } else {
      L.polyline(l.shape, { pane: "shapes", color: "#fff", weight: 9 }).addTo(planLayer);
      L.polyline(l.shape, { pane: "shapes", color: "#" + l.color, weight: 6 }).addTo(planLayer);
      l.stops.forEach((s, k) => {
        const end = k === 0 || k === l.stops.length - 1;
        L.circleMarker([s.lat, s.lon], { renderer: focusCanvas, radius: end ? 6 : 3.5, color: "#" + l.color, weight: end ? 3 : 2, fillColor: "#fff", fillOpacity: 1 })
          .bindTooltip(`${esc(titleCase(s.name))} · ${fmtTime(s.time)}`, { className: "mv-tip", direction: "top" }).addTo(planLayer);
      });
      all.push(...l.shape);
    }
  }
  if (doFit) fit(L.latLngBounds(all), 16);
}

// ------------------------------------------------------------------ interazioni mappa
map.on("click", (e) => {
  const hv = hitVehicle(e.layerPoint);
  if (hv) { openView({ type: "vehicle", id: hv.d.id }); return; }
  const P = S.plan;
  if (!(S.tab === "plan" && P.picking)) {
    const hs = hitStop(e.layerPoint);
    if (hs != null) { openView({ type: "stop", i: hs }); return; }
  }
  if (S.tab === "plan" && P.picking) {
    P[P.picking] = { name: `Punto sulla mappa (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)})`, lat: e.latlng.lat, lon: e.latlng.lng };
    P.picking = null;
    drawPlanPins();
    render(true);
    runPlan();
    return;
  }
  // clic su un punto vuoto della mappa: si esce dalla vista del mezzo o della linea
  if (["vehicle", "route"].includes(S.stack[S.stack.length - 1]?.type)) closeFocusView();
});

// chiude le viste mezzo/linea in cima alla pila, tornando alla vista da cui erano state aperte
function closeFocusView() {
  const focus = (v) => v.type === "vehicle" || v.type === "route";
  while (S.stack.length > 1 && focus(S.stack[S.stack.length - 1])) S.stack.pop();
  if (focus(S.stack[S.stack.length - 1])) S.stack = [{ type: S.tab }];
  render();
}
map.on("contextmenu", (e) => {
  const pt = { lat: e.latlng.lat, lon: e.latlng.lng, name: `Punto sulla mappa (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)})` };
  const el = document.createElement("div");
  el.className = "ctx-menu";
  el.innerHTML = `<button data-a="from">${I.pin} Parti da qui</button><button data-a="to">${I.target} Arriva qui</button><button data-a="near">${I.stop} Fermate vicine</button>`;
  const pop = L.popup({ closeButton: false, offset: [0, -4] }).setLatLng(e.latlng).setContent(el).openOn(map);
  el.addEventListener("click", async (ev) => {
    const a = ev.target.closest("button")?.dataset.a;
    if (!a) return;
    map.closePopup(pop);
    if (a === "near") {
      const res = await api(`/api/nearby?lat=${pt.lat}&lon=${pt.lon}&r=400`);
      if (res.length) openView({ type: "stop", i: res[0].i }); else toast("Nessuna fermata entro 400 m");
      return;
    }
    S.plan[a] = pt;
    if (S.tab !== "plan") setTab("plan"); else render(true);
    drawPlanPins();
    runPlan();
  });
});

// ------------------------------------------------------------------ geolocalizzazione
function getPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) { toast("Geolocalizzazione non disponibile"); reject(); return; }
    navigator.geolocation.getCurrentPosition((p) => {
      S.me = [p.coords.latitude, p.coords.longitude];
      const icon = L.divIcon({ className: "veh-wrap", iconSize: [18, 18], html: '<div class="me-dot"></div>' });
      if (meMarker) meMarker.setLatLng(S.me); else meMarker = L.marker(S.me, { icon, zIndexOffset: 3000 }).addTo(map);
      resolve(S.me);
    }, () => { toast("Posizione non disponibile"); reject(); }, { enableHighAccuracy: true, timeout: 10000 });
  });
}
function locate() {
  getPosition().then((p) => {
    panTo(p, 16);
    if (S.stack[S.stack.length - 1]?.type === "explore") render(true);
  }).catch(() => {});
}

// ------------------------------------------------------------------ controlli
$$("#mode-filter .chip").forEach((b) => b.addEventListener("click", () => {
  const m = +b.dataset.mode;
  S.filter[m] = !S.filter[m];
  b.classList.toggle("on", S.filter[m]);
  applyVehicleFilter();
}));
$("#btn-est").addEventListener("click", (e) => {
  S.busEst = !S.busEst;
  e.currentTarget.classList.toggle("on", S.busEst);
  toast(S.busEst ? "Mostro anche i bus senza GPS (posizione stimata)" : "Bus senza GPS nascosti");
  pollVehicles();
});
$("#btn-locate").addEventListener("click", locate);
$("#btn-theme").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light"));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !e.target.closest("input")) {
    if (S.stack[S.stack.length - 1]?.type === "vehicle") closeVehicle(); else back();
  }
});

// bottom sheet su mobile
function setSheet(state) {
  const p = $("#panel");
  p.classList.remove("collapsed", "expanded");
  if (state !== "normal") p.classList.add(state);
}
// il foglio segue il dito (maniglia e intestazione) e si aggancia alla posizione più vicina,
// tenendo conto della velocità del gesto; un tocco senza trascinare passa alla posizione successiva
(() => {
  const p = $("#panel");
  const STATES = ["collapsed", "normal", "expanded"];
  let drag = null;
  const heights = () => STATES.map((s) => {
    p.classList.add("dragging");
    const old = p.className;
    setSheet(s);
    const h = p.getBoundingClientRect().height;
    p.className = old;
    return h;
  });
  const start = (e) => {
    if (!isMobile() || e.target.closest("button, a, input")) return;
    const hs = heights();
    drag = { y0: e.clientY, h0: p.getBoundingClientRect().height, hs, last: e.clientY, t: performance.now(), v: 0 };
    p.classList.add("dragging");
    p.style.height = drag.h0 + "px";
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    if (!drag) return;
    const now = performance.now();
    drag.v = (e.clientY - drag.last) / Math.max(1, now - drag.t);   // px/ms, positivo = verso il basso
    drag.last = e.clientY;
    drag.t = now;
    const h = Math.min(drag.hs[2] + 20, Math.max(drag.hs[0] - 20, drag.h0 - (e.clientY - drag.y0)));
    p.style.height = h + "px";
  };
  const end = (e) => {
    if (!drag) return;
    const moved = Math.abs(e.clientY - drag.y0);
    const h = p.getBoundingClientRect().height;
    const cur = STATES.findIndex((s) => s === "normal" ? !p.classList.contains("collapsed") && !p.classList.contains("expanded") : p.classList.contains(s));
    let next;
    if (moved < 6) next = cur === 2 ? 1 : cur + 1;                       // tocco
    else {
      // la posizione più vicina a dove è arrivato il dito; un gesto rapido la spinge nel suo verso
      next = drag.hs.reduce((bi, hh, i) => Math.abs(hh - h) < Math.abs(drag.hs[bi] - h) ? i : bi, 0);
      if (drag.v < -0.5 && drag.hs[next] < h) next = Math.min(2, next + 1);
      if (drag.v > 0.5 && drag.hs[next] > h) next = Math.max(0, next - 1);
    }
    drag = null;
    p.style.height = "";
    p.classList.remove("dragging");
    setSheet(STATES[next]);
  };
  for (const el of [$("#sheet-handle"), $(".panel-head")]) {
    el.addEventListener("pointerdown", start);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }
})();

// ------------------------------------------------------------------ avvio
async function boot() {
  const msg = $("#splash-msg");
  for (;;) {
    const st = await pollStatus();
    if (st && st.ready) break;
    msg.textContent = st ? (st.error ? "Errore: " + st.error : st.message) : "In attesa del server…";
    await new Promise((r) => setTimeout(r, 250));
  }
  msg.textContent = "Carico linee e fermate…";
  // tutto in parallelo: i mezzi si scaricano mentre arrivano linee e fermate
  const vehicles = api(`/api/vehicles?bus_est=${S.busEst ? 1 : 0}`).catch(() => null);
  const [routes, stops] = await Promise.all([api("/api/routes"), api("/api/stops")]);
  S.routes = routes;
  S.stops = stops;
  applyVehicles(await vehicles);
  $("#splash").classList.add("hidden");
  setTab("explore");
  buildStops();
  pollAlerts();
  vehicleLoop(S.feedTs ? 10000 : 1000);
  setInterval(pollStatus, 10000);
  setInterval(pollAlerts, 120000);
  setInterval(() => { if (S.stack[S.stack.length - 1]?.type === "explore" && !$("#q")?.value) render(true); }, 30000);
}
boot();

// app installabile: il service worker salva l'interfaccia sul telefono per aprirla subito
// (i browser lo consentono solo in HTTPS o su localhost)
if ("serviceWorker" in navigator && (location.protocol === "https:" || LOCAL)) {
  navigator.serviceWorker.register("sw.js").catch((e) => console.warn("Service worker non registrato", e));
}

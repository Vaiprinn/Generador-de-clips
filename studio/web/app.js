/* ClipForge Studio — interfaz (sin frameworks). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const K = window.CF_TOKEN;

// ---------------------------------------------------------------- estado
const S = {
  presets: [], aiKeys: {}, preset: "gta",
  style: {}, ai: "ninguno", framing: "auto",
  video: null, view: "home", moments: null, episode: null,
};

const LAYOUTS = [
  ["auto", "Auto", []],
  ["facecam_esquina", "Esquina", [[55, 6, 38, 22]]],
  ["split", "Split", [[0, 0, 100, 42]]],
  ["blur", "Blur", [[0, 34, 100, 32]]],
  ["sin_facecam", "Solo juego", [[0, 0, 100, 100]]],
  ["blur_cam", "Cámara arriba", [[8, 4, 84, 26], [0, 52, 100, 26]]],
  ["seguimiento", "Seguimiento EA FC", [[36, 4, 58, 24], [6, 12, 29, 7], [0, 30, 100, 50]]],
];
const TRACK_MODES = [["no", "Fijo"], ["movimiento", "Sigue el movimiento"], ["indicador", "Sigue a tu jugador (EA FC)"]];
const AI = [
  ["ninguno", "Sin IA · picos de volumen (gratis)"],
  ["ollama", "Ollama · IA local (gratis)"],
  ["claude", "Claude · API (de pago)"],
  ["openai", "OpenAI · API (de pago)"],
];
const TIPS = [
  "Graba el micro en una pista aparte en OBS: el detector acierta mucho más.",
  "Lo ya medido se guarda: repetir con el mismo directo es mucho más rápido.",
  "Los clips que se entienden sin contexto son los que más funcionan.",
  "Apaga la radio de GTA al grabar: la música con copyright silencia los clips.",
];

// ---------------------------------------------------------------- utilidades
async function api(path, body) {
  const opt = { headers: { "X-Token": K } };
  if (body !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* sin JSON */ }
    throw new Error(msg);
  }
  return r.json();
}
const url = (path, params = {}) => `${path}?${new URLSearchParams({ ...params, k: K })}`;
const mediaUrl = (p) => url("/api/media", { path: p });
// Botón para bajar el vídeo (en el iPhone: se abre y con Compartir → Guardar vídeo va a Fotos).
const saveBtn = (p) => `<a class="btn sm" href="${url("/api/media", { path: p, dl: 1 })}" download data-save="${esc(p)}">⬇️ Guardar en este dispositivo</a>`;
// En el PC (ventana de Studio) se elige dónde guardarlo; en el móvil el enlace descarga normal.
document.addEventListener("click", async (e) => {
  const a = e.target.closest?.("a[data-save]");
  if (!a || !window.pywebview?.api?.save_video) return;
  e.preventDefault();
  const r = await window.pywebview.api.save_video(a.dataset.save);
  if (r?.error) toast(r.error, true);
  else if (r?.saved) toast("Guardado en " + r.saved);
});
const frameUrl = (p, t, w = 640) => url("/api/frame", { path: p, t: (+t).toFixed(1), w });
const pngUrl = (u) => `${u}&k=${encodeURIComponent(K)}`;

function fmt(t, withTenths = false) {
  t = Math.max(0, +t || 0);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const ss = withTenths ? s.toFixed(1).padStart(4, "0") : String(Math.floor(s)).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}
function fmtDur(t) {
  t = Math.round(t || 0);
  const h = Math.floor(t / 3600), m = Math.round((t % 3600) / 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : t >= 60 ? `${Math.round(t / 60)} min` : `${t} s`;
}
let toastTimer;
function toast(msg, error = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast"), error ? 6000 : 3000);
}
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copiado ✓"); } catch (_) { toast("No se pudo copiar", true); }
}
const savePrefs = () => api("/api/prefs", { preset: S.preset, ai: S.ai, framing: S.framing }).catch(() => {});
const presetInfo = () => S.presets.find((p) => p.id === S.preset) || S.presets[0];

function crumbs(items) {
  S.onStyleChange = null;  // cada vista decide si refresca algo al cambiar el estilo
  $("#crumbs").innerHTML = items.map((it, i) =>
    (i ? '<span class="sep">›</span>' : "") + (it.go ? `<a data-go="${i}">${esc(it.t)}</a>` : `<span>${esc(it.t)}</span>`)).join("");
  $$("#crumbs a").forEach((a) => (a.onclick = () => items[+a.dataset.go].go()));
}

// ---------------------------------------------------------------- trabajos con progreso
async function runJob(title, start) {
  const modal = $("#job-modal");
  $("#job-title").textContent = title;
  $("#job-msg").textContent = "Empezando…";
  $("#job-bar").style.width = "0";
  $("#job-pct").textContent = "0 %";
  $("#job-tip").textContent = "💡 " + TIPS[Math.floor(Math.random() * TIPS.length)];
  modal.classList.add("show");
  const t0 = Date.now();
  try {
    const { job } = await start();
    while (true) {
      await new Promise((r) => setTimeout(r, 900));
      const j = await api(`/api/jobs/${job}`);
      const pct = Math.round(j.progress * 100);
      $("#job-bar").style.width = pct + "%";
      $("#job-pct").textContent = pct + " %";
      $("#job-msg").textContent = j.message || "";
      $("#job-time").textContent = fmt((Date.now() - t0) / 1000);
      if (j.status === "listo") return j.result;
      if (j.status === "error") throw new Error(j.error);
    }
  } finally {
    modal.classList.remove("show");
  }
}

// ---------------------------------------------------------------- elegir vídeo
async function pickVideo() {
  if (window.pywebview?.api?.pick_video) {
    const path = await window.pywebview.api.pick_video();
    if (path) openVideo(path);
    return;
  }
  // Móvil o navegador: subir desde el dispositivo o elegir un vídeo que ya está en el PC.
  const modal = $("#pick-modal");
  const close = () => { modal.classList.remove("show"); modal.setAttribute("aria-hidden", "true"); };
  modal.classList.add("show");
  modal.setAttribute("aria-hidden", "false");
  $("#pick-close").onclick = close;
  $("#up-msg").textContent = "";
  $("#up-bar").classList.add("hidden");
  $("#pick-file").value = "";
  $("#pick-file").onchange = () => {
    const f = $("#pick-file").files[0];
    if (!f) return;
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url("/api/upload", { name: f.name || "video.mov" }));
    xhr.setRequestHeader("X-Token", K);
    $("#up-bar").classList.remove("hidden");
    const t0 = Date.now();
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pc = e.loaded / e.total;
      $("#up-bar div").style.width = (pc * 100).toFixed(1) + "%";
      const mbs = e.loaded / 1048576 / Math.max(0.5, (Date.now() - t0) / 1000);
      $("#up-msg").textContent = `Subiendo… ${Math.round(pc * 100)} % · ${(e.loaded / 1048576).toFixed(0)} de ${(e.total / 1048576).toFixed(0)} MB · ${mbs.toFixed(1)} MB/s`;
    };
    xhr.onload = () => {
      if (xhr.status !== 200) { $("#up-msg").textContent = "No se pudo subir: " + xhr.responseText; return; }
      close();
      openVideo(JSON.parse(xhr.responseText).path);
    };
    xhr.onerror = () => ($("#up-msg").textContent = "Se cortó la conexión con el PC.");
    xhr.send(f);
  };
  try {
    const { videos } = await api("/api/browse");
    $("#pick-list").innerHTML = videos.length ? videos.map((v, i) => `
      <button class="pick-item" data-i="${i}">
        <img src="${frameUrl(v.path, 3, 160)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
        <span><b>${esc(v.name)}</b><small class="muted">${esc(v.folder)} · ${(v.size / 1048576).toFixed(0)} MB · ${new Date(v.mtime * 1000).toLocaleDateString()}</small></span>
      </button>`).join("") : '<div class="muted small">No hay vídeos en Vídeos, Escritorio ni Descargas.</div>';
    $$(".pick-item").forEach((b) => (b.onclick = () => { close(); openVideo(videos[+b.dataset.i].path); }));
  } catch (e) { $("#pick-list").innerHTML = `<div class="muted small">${esc(e.message)}</div>`; }
}

// ---------------------------------------------------------------- móvil
async function phonePanel() {
  const modal = $("#phone-modal");
  const close = () => { modal.classList.remove("show"); modal.setAttribute("aria-hidden", "true"); };
  $("#phone-close").onclick = close;
  const show = (info) => {
    $("#phone-on").checked = info.on;
    $("#phone-body").innerHTML = info.error ? `<p class="error">${esc(info.error)}</p>` : info.on ? `
      <div class="qr">${info.qr}</div>
      <ol class="small steps">
        <li>Abre la <b>cámara del iPhone</b>, apunta al código y toca el enlace.</li>
        <li>En Safari: <b>Compartir</b> → <b>Añadir a pantalla de inicio</b>. Tendrás ClipForge como una app.</li>
        <li>Si no carga: cuando Windows pregunte en este PC, <b>permite Python en redes privadas</b>, y comprueba que el móvil está en la misma Wi-Fi.</li>
      </ol>
      <p class="muted small" style="word-break:break-all">${esc(info.url)}</p>` : "";
  };
  $("#phone-on").onchange = async () => {
    try { show(await api("/api/phone", { on: $("#phone-on").checked })); } catch (e) { toast(e.message, true); }
  };
  modal.classList.add("show");
  modal.setAttribute("aria-hidden", "false");
  try { show(await api("/api/phone")); } catch (e) { toast(e.message, true); }
}
async function openVideo(path) {
  try {
    S.video = await api("/api/probe", { path });
    S.moments = null;
    S.episode = null;
    viewVideo();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- vista: inicio
async function viewHome() {
  S.view = "home";
  crumbs([{ t: "Inicio" }]);
  const v = $("#view");
  v.innerHTML = `
    <section class="hero">
      <h1>¿Qué vamos a crear hoy?</h1>
      <p>Elige un clip, un directo entero o cualquier vídeo. Después decides qué hacer con él.</p>
    </section>
    <div class="drop" id="drop">
      <div class="big-icon">🎬</div>
      <h2>Elegir vídeo</h2>
      <p>Haz clic o arrastra aquí un archivo. Se lee directamente del disco, sin copiar nada.</p>
      <div class="kinds">
        <span class="badge">✂️ Clip → Short</span><span class="badge">🔎 Directo → mejores momentos</span><span class="badge">📺 Directo → episodio de YouTube</span>
      </div>
    </div>
    <div class="section-title"><h2>Seguir donde lo dejaste</h2><span class="muted small" id="proj-count"></span></div>
    <div class="projects" id="projects"><div class="muted">Cargando…</div></div>`;
  const drop = $("#drop");
  drop.onclick = pickVideo;
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    const f = e.dataTransfer.files[0];
    const p = f && (f.pywebviewFullPath || f.path);
    if (p) openVideo(p);
    else toast("Arrastrar no da la ruta aquí: haz clic y elige el archivo.", true);
  };
  try {
    const { projects } = await api("/api/projects");
    $("#proj-count").textContent = projects.length ? `${projects.length} proyectos` : "";
    $("#projects").innerHTML = projects.length ? projects.map((p, i) => `
      <div class="card project" data-i="${i}">
        <img src="${frameUrl(p.vod, 60, 240)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
        <div>
          <b title="${esc(p.name)}">${esc(p.name)}</b>
          <div class="row" style="gap:6px;margin-top:4px">
            ${p.has_moments ? `<span class="badge">${p.clips ? p.clips + " Shorts" : "Momentos"}</span>` : ""}
            ${p.has_episode ? `<span class="badge ${p.episode_done ? "ok" : ""}">${p.episode_done ? "Episodio ✓" : "Episodio"}</span>` : ""}
          </div>
        </div>
      </div>`).join("") : '<div class="empty">Aún no hay proyectos. ¡Empieza eligiendo un vídeo!</div>';
    $$(".project").forEach((el) => (el.onclick = async () => {
      const p = projects[+el.dataset.i];
      await openVideo(p.vod);
      if (!S.video) return;
      if (p.has_moments) loadMoments(p.dir);
      else if (p.has_episode) loadEpisode(p.dir + "\\youtube");
    }));
  } catch (e) { $("#projects").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ---------------------------------------------------------------- vista: vídeo elegido
function viewVideo() {
  S.view = "video";
  const vd = S.video;
  crumbs([{ t: "Inicio", go: viewHome }, { t: vd.name }]);
  const long = vd.duration >= 600;
  const tasks = [
    { id: "short", ico: "✂️", t: "Convertir en Short", d: "Este vídeo ya es un clip: sale vertical 9:16 con subtítulos, gancho y tu estilo.", rec: !long },
    { id: "momentos", ico: "🔎", t: "Sacar los mejores momentos", d: "Busca solo los momentazos del directo y los convierte en varios Shorts que tú revisas.", rec: long },
    { id: "episodio", ico: "📺", t: "Episodio de YouTube", d: "Vídeo 16:9 con los mejores tramos, sin silencios, capítulos, intro y miniatura.", rec: false },
  ];
  const p = vd.project;
  $("#view").innerHTML = `
    <div class="video-head">
      <div>
        <img src="${frameUrl(vd.path, Math.min(60, vd.duration / 3), 640)}" alt="">
        <div class="facts">
          <div class="fact"><small>Duración</small><b>${fmtDur(vd.duration)}</b></div>
          <div class="fact"><small>Resolución</small><b>${vd.width}×${vd.height} · ${Math.round(vd.fps)} fps</b></div>
          <div class="fact"><small>Pistas de audio</small><b>${vd.audio_tracks}</b></div>
          <div class="fact"><small>Tamaño</small><b>${vd.size_mb >= 1024 ? (vd.size_mb / 1024).toFixed(1) + " GB" : vd.size_mb + " MB"}</b></div>
        </div>
        ${vd.audio_tracks === 1 ? `<div class="note warn" style="margin-top:12px">🎙️ Una sola pista de audio: el volumen del juego cuenta como tus gritos. En OBS graba el micro en una pista aparte.</div>` : ""}
      </div>
      <div>
        <h1 style="font-size:26px;word-break:break-word">${esc(vd.name)}</h1>
        <p class="muted" style="margin:6px 0 18px">¿Qué quieres hacer con este vídeo?</p>
        <div class="tasks">
          ${tasks.map((t) => `
            <button class="task" data-task="${t.id}">
              <span class="ico">${t.ico}</span>
              <span><h3>${t.t} ${t.rec ? '<span class="badge accent">Recomendado</span>' : ""}</h3><p>${t.d}</p></span>
              <span class="go">→</span>
            </button>`).join("")}
        </div>
        ${p && (p.has_moments || p.has_episode) ? `
          <div class="note" style="margin-top:16px">📁 Ya trabajaste con este vídeo.
            ${p.has_moments ? `<a href="#" id="resume-m" style="color:var(--accent)">Ver los momentos</a>` : ""}
            ${p.has_episode ? ` · <a href="#" id="resume-e" style="color:var(--accent)">Ver el episodio</a>` : ""}
          </div>` : ""}
      </div>
    </div>`;
  $$(".task").forEach((b) => (b.onclick = () => ({ short: viewShort, momentos: viewMomentsConfig, episodio: viewEpisodeConfig })[b.dataset.task]()));
  $("#resume-m")?.addEventListener("click", (e) => { e.preventDefault(); loadMoments(p.dir); });
  $("#resume-e")?.addEventListener("click", (e) => { e.preventDefault(); loadEpisode(p.dir + "\\youtube"); });
}

// bloque común: IA
function aiField(id = "ai") {
  return `<label class="field"><span>IA que elige y escribe los textos</span>
    <select id="${id}">${AI.map(([v, l]) => `<option value="${v}" ${v === S.ai ? "selected" : ""}>${l}</option>`).join("")}</select>
    <small class="muted" id="${id}-note"></small></label>`;
}
function bindAi(id = "ai") {
  const sel = $("#" + id), note = $("#" + id + "-note");
  const upd = () => {
    S.ai = sel.value;
    savePrefs();
    const need = { claude: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY" }[S.ai];
    note.textContent = need && !S.aiKeys[S.ai] ? `⚠️ Falta ${need} en el archivo .env: se usarán los picos de volumen.`
      : S.ai === "ollama" ? "Necesita Ollama abierto en tu PC." : S.ai === "ninguno" ? "Gratis. Sin gancho ni título automáticos (los escribes tú)." : "";
  };
  sel.onchange = upd;
  upd();
}
function framingField() {
  return `<label class="field"><span>Encuadre del Short</span><div class="layouts" id="framing">${LAYOUTS.map(([v, l, boxes]) => `
    <button data-v="${v}" class="${v === S.framing ? "on" : ""}"><span class="mini">${boxes.map((b) => `<i style="left:${b[0]}%;top:${b[1]}%;width:${b[2]}%;height:${b[3]}%"></i>`).join("") || '<i style="left:30%;top:30%;width:40%;height:40%;border-radius:50%;background:none;border:2px solid var(--accent)"></i>'}</span>${l}</button>`).join("")}
    </div><small class="muted">Auto busca tu webcam; si no sales en cámara, usa Blur. <b>Seguimiento EA FC</b>: cámara arriba con el marcador a su izquierda y, debajo, el juego con plano abierto siguiendo la jugada.</small></label>
    <div class="gp-controls" id="gp-controls"></div>`;
}
// Controles del juego: acercar, mover y seguimiento (según el encuadre elegido).
function renderGpControls(onChange) {
  const box = $("#gp-controls");
  if (!box) return;
  const st = S.style, f = S.framing;
  const zoom = st["layout.gameplay_zoom"] ?? 1, ox = st["layout.gameplay_offset"] ?? 0, oy = st["layout.gameplay_offset_y"] ?? 0;
  const tops = f === "blur_cam" || f === "seguimiento";
  box.innerHTML = `
    <b class="small">🎮 El juego</b>
    <label class="field"><span>Zoom <b id="gz-v">${(+zoom).toFixed(2)}×</b></span><input type="range" min="${f === "seguimiento" ? 0.6 : 1}" max="2.5" step="0.05" data-gk="layout.gameplay_zoom" value="${zoom}"></label>
    <div class="row">
      <label class="field"><span>← Mover →</span><input type="range" min="-1" max="1" step="0.02" data-gk="layout.gameplay_offset" value="${ox}"></label>
      <label class="field"><span>↑ Mover ↓</span><input type="range" min="-1" max="1" step="0.02" data-gk="layout.gameplay_offset_y" value="${oy}"></label>
    </div>
    ${!["blur", "blur_cam"].includes(f) ? `<label class="field"><span>Cámara del juego</span><select data-gk="layout.track">${TRACK_MODES.map(([v, l]) => `<option value="${v}" ${(st["layout.track"] || "indicador") === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <small class="muted">«Sigue a tu jugador» busca el triángulo de color sobre el jugador que controlas; si no se ve, sigue el movimiento.</small></label>` : ""}
    ${tops ? `<div class="row">
      <label class="row small" style="gap:6px"><input type="checkbox" data-gk="layout.hud_score" ${(st["layout.hud_score"] ?? f === "seguimiento") ? "checked" : ""}> Marcador</label>
      <label class="row small" style="gap:6px"><input type="checkbox" data-gk="layout.hud_radar" ${st["layout.hud_radar"] ? "checked" : ""}> Radar</label>
      <label class="field grow"><span>Tamaño cámara</span><input type="range" min="0.5" max="1" step="0.02" data-gk="layout.top_cam_width" value="${st["layout.top_cam_width"] ?? (f === "seguimiento" ? 0.58 : 0.86)}"></label></div>` : ""}
    <button class="btn sm ghost" id="gp-reset">↺ Centrar el juego</button>`;
  if (!["blur", "blur_cam"].includes(f) && st["layout.track"] == null) st["layout.track"] = "indicador";
  $$("[data-gk]", box).forEach((el) => {
    el.addEventListener(el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input", () => {
      const k = el.dataset.gk;
      st[k] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : +el.value;
      if (k === "layout.gameplay_zoom") $("#gz-v").textContent = (+el.value).toFixed(2) + "×";
      onChange?.(k);
    });
  });
  $("#gp-reset").onclick = (e) => {
    e.preventDefault();
    Object.assign(st, { "layout.gameplay_zoom": 1, "layout.gameplay_offset": 0, "layout.gameplay_offset_y": 0 });
    renderGpControls(onChange);
    onChange?.("reset");
  };
}
function bindFraming(onChange) {
  $$("#framing button").forEach((b) => (b.onclick = (e) => {
    e.preventDefault();
    S.framing = b.dataset.v;
    savePrefs();
    $$("#framing button").forEach((x) => x.classList.toggle("on", x === b));
    renderGpControls(onChange);
    onChange?.("framing");
  }));
  renderGpControls(onChange);
}

/* Vista previa en vivo conectada a un <video>: pide la geometría al servidor y el seguimiento. */
function attachLive(container, video, path, getRange, getHook, noteEl) {
  const lp = new window.LivePreview(container, video, K);
  let trackKey = "";
  if (noteEl) noteEl.textContent = "Buscando tu webcam… (la primera vez tarda unos segundos)";
  const refresh = debounce(async (why) => {
    try {
      const g = await api("/api/layout", { path, preset: S.preset, style: S.style, framing: S.framing, hook: getHook?.() || "" });
      lp.setGeometry(g);
      if (noteEl) noteEl.textContent = g.note;
      const r = getRange();
      lp.setRange(r);
      if (g.track) {
        const key = JSON.stringify([path, r, S.framing, S.style["layout.track"], S.style["layout.gameplay_zoom"]]);
        if (key !== trackKey) {
          trackKey = key;
          if (noteEl) noteEl.textContent = g.note + " · calculando el seguimiento…";
          const tr = await api("/api/track", { path, preset: S.preset, style: S.style, framing: S.framing, start: r[0], end: r[1] });
          lp.setTrack(tr);
          if (noteEl) noteEl.textContent = g.note + " · seguimiento listo";
        }
      } else lp.setTrack(null);
    } catch (e) { if (noteEl) noteEl.textContent = e.message; }
  }, 350);
  refresh();
  return { lp, refresh };
}

// ---------------------------------------------------------------- momentos: configurar
function viewMomentsConfig() {
  S.view = "moments-config";
  const vd = S.video;
  crumbs([{ t: "Inicio", go: viewHome }, { t: vd.name, go: viewVideo }, { t: "Mejores momentos" }]);
  let chat = null;
  $("#view").innerHTML = `
    <div class="config">
      <div class="card stack">
        <div><h2>🔎 Sacar los mejores momentos</h2>
          <p class="muted">ClipForge mide tu voz, los cambios de escena y el chat en todo el directo, transcribe los mejores tramos y te propone los clips.</p></div>
        <label class="field"><span>¿Cuántos clips como mucho? <b id="n-val">8</b></span><input type="range" id="n" min="1" max="15" value="8"></label>
        <label class="field"><span>Duración máxima de cada clip</span>
          <div class="seg" id="maxlen">${[30, 45, 60, 90].map((s) => `<button data-v="${s}" class="${s === 60 ? "on" : ""}">${s} s</button>`).join("")}</div></label>
        ${aiField()}
        <label class="field"><span>Chat del directo (opcional)</span>
          <div class="row"><button class="btn sm" id="chat-btn">💬 Elegir archivo de chat</button><span class="muted small" id="chat-name">JSON de chat-downloader o .txt con la hora en cada línea</span></div></label>
        <div class="actions"><button class="btn primary big" id="go">🔎 Buscar momentos</button>
          <span class="muted small">Con ${fmtDur(vd.duration)} de vídeo tarda ~${Math.max(1, Math.round(vd.duration / 3600 * 2 + 1))}-${Math.max(2, Math.round(vd.duration / 3600 * 4 + 2))} min en tu PC.</span></div>
      </div>
      <div class="card stack">
        <h3>Así quedarán los Shorts <span class="muted small">· ▶ para verlo</span></h3>
        <div id="live"></div><p class="muted small" id="pv-note" style="text-align:center"></p>
        <video playsinline id="live-src" src="${mediaUrl(vd.path)}" preload="metadata" style="display:none"></video>
        ${framingField()}
        <div class="note">🎨 Subtítulos, gancho, colores y extras: en <b>Mi estilo</b> (arriba a la derecha).</div></div>
    </div>`;
  $("#n").oninput = (e) => ($("#n-val").textContent = e.target.value);
  let maxLen = 60;
  $$("#maxlen button").forEach((b) => (b.onclick = (e) => { e.preventDefault(); maxLen = +b.dataset.v; $$("#maxlen button").forEach((x) => x.classList.toggle("on", x === b)); }));
  bindAi();
  const sample = Math.min(Math.max(30, vd.duration * 0.3), Math.max(0, vd.duration - 30));
  const lsrc = $("#live-src");
  lsrc.addEventListener("loadedmetadata", () => (lsrc.currentTime = sample), { once: true });
  const live = attachLive($("#live"), lsrc, vd.path, () => [sample, Math.min(vd.duration, sample + 30)], () => "QUÉ LOCURA", $("#pv-note"));
  bindFraming(() => live.refresh());
  S.onStyleChange = () => live.refresh();
  $("#chat-btn").onclick = async (e) => {
    e.preventDefault();
    chat = window.pywebview?.api?.pick_chat ? await window.pywebview.api.pick_chat() : prompt("Ruta del archivo de chat:");
    if (chat) $("#chat-name").textContent = chat.split(/[\\/]/).pop();
  };
  $("#go").onclick = async () => {
    try {
      const res = await runJob("Buscando los mejores momentos…", () => api("/api/moments/find", {
        path: vd.path, preset: S.preset, style: S.style, ai: S.ai, max_clips: +$("#n").value, max_len: maxLen, chat,
      }));
      S.moments = { dir: res.dir, vod: vd.path, clips: res.clips.map((c) => ({ ...c, keep: true })), notes: res.notes, duration: res.duration, rendered: {} };
      viewReview();
    } catch (e) { toast(e.message, true); }
  };
}

async function loadMoments(dir) {
  try {
    const m = await api(url("/api/moments", { dir }));
    S.moments = { ...m, clips: m.clips.map((c) => ({ keep: true, ...c })) };
    viewReview();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- reproductor de un tramo del VOD
function segmentPlayer(el, src, getRange) {
  const video = $("video", el), btn = $(".play", el), bar = $(".scrub i", el), tc = $(".tc", el);
  let loaded = false;
  const range = () => getRange();
  const show = () => {
    const [s, e] = range();
    tc.textContent = fmt(Math.max(0, video.currentTime - s)) + " / " + fmt(e - s);
    bar.style.width = Math.min(100, ((video.currentTime - s) / (e - s)) * 100) + "%";
  };
  btn.onclick = () => {
    const [s] = range();
    if (!loaded) { video.src = src; loaded = true; }
    video.currentTime = s;
    video.play();
    btn.classList.add("hidden");
  };
  video.onclick = () => (video.paused ? video.play() : video.pause());
  video.ontimeupdate = () => {
    const [, e] = range();
    show();
    if (video.currentTime >= e) { video.pause(); btn.classList.remove("hidden"); }
  };
  video.onpause = () => { if (video.currentTime >= range()[1] - 0.2) btn.classList.remove("hidden"); };
  return { seek: (t) => { if (loaded) video.currentTime = t; show(); }, stop: () => video.pause() };
}

// ---------------------------------------------------------------- momentos: revisar
function viewReview() {
  S.view = "review";
  const m = S.moments;
  const name = (m.vod || "").split(/[\\/]/).pop();
  crumbs([{ t: "Inicio", go: viewHome }, { t: name, go: S.video ? viewVideo : viewHome }, { t: "Revisar clips" }]);
  const count = () => m.clips.filter((c) => c.keep).length;
  $("#view").innerHTML = `
    <div class="toolbar">
      <div class="grow"><b id="sel-count"></b> <span class="muted small">· Escucha cada clip, ajusta inicio y fin, y escribe el gancho.</span></div>
      <button class="btn sm ghost" id="all-on">Aprobar todos</button>
      <button class="btn sm ghost" id="all-off">Ninguno</button>
      <button class="btn" id="style-btn">🎨 Estilo</button>
      <button class="btn primary" id="render">🎬 Crear Shorts</button>
    </div>
    ${m.notes?.length ? `<div class="note" style="margin-bottom:16px">ℹ️ ${m.notes.map(esc).join("<br>")}</div>` : ""}
    <details class="card" id="enc" style="margin-bottom:16px" ${S.framing === "seguimiento" || S.framing === "blur_cam" ? "open" : ""}>
      <summary style="cursor:pointer"><b>🎯 Encuadre de los Shorts</b> <span class="muted small">· cámara, juego, seguimiento, marcador y radar (con vista previa en vivo)</span></summary>
      <div class="config" style="margin-top:14px">
        <div>${framingField()}</div>
        <div><div id="live"></div><p class="muted small" id="pv-note" style="text-align:center"></p>
          <video playsinline id="live-src" src="${mediaUrl(m.vod)}" preload="metadata" style="display:none"></video></div>
      </div>
    </details>
    <div class="clips" id="clips"></div>`;
  const upd = () => {
    $("#sel-count").textContent = `${count()} de ${m.clips.length} clips aprobados`;
    $("#render").textContent = `🎬 Crear ${count()} Shorts`;
    $("#render").disabled = !count();
  };
  const box = $("#clips");
  m.clips.forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "card clip " + (c.keep ? "on" : "off");
    el.innerHTML = `
      <div class="player"><video playsinline preload="none" poster="${frameUrl(m.vod, c.start + 1, 480)}"></video>
        <button class="play" title="Reproducir el clip">▶</button><span class="tc"></span><div class="scrub"><i></i></div></div>
      <div class="clip-body">
        <div class="clip-top"><span class="score ${c.score >= 8 ? "hi" : ""}">${Math.round(c.score)}</span>
          <div class="why grow">${esc(c.why || "Momento detectado")}</div></div>
        <div class="timing">
          ${["start", "end"].map((k) => `
            <div class="tbox"><small>${k === "start" ? "Empieza" : "Termina"}</small><span class="tv" data-k="${k}"></span>
              <div class="nudge"><button data-k="${k}" data-d="-5">−5</button><button data-k="${k}" data-d="-1">−1</button><button data-k="${k}" data-d="1">+1</button><button data-k="${k}" data-d="5">+5</button></div></div>`).join("")}
        </div>
        <input type="text" class="hook-input" placeholder="GANCHO EN PANTALLA (opcional)" value="${esc(c.hook)}">
        <input type="text" class="title-input" placeholder="Título para publicar" value="${esc(c.title)}">
        <div class="approve"><span class="muted small dur"></span>
          <label class="row" style="gap:8px;cursor:pointer"><span class="small"><b>Aprobado</b></span><span class="toggle"><input type="checkbox" ${c.keep ? "checked" : ""}><i></i></span></label></div>
      </div>`;
    box.appendChild(el);
    const player = segmentPlayer($(".player", el), mediaUrl(m.vod), () => [c.start, c.end]);
    const showT = () => {
      $$(".tv", el).forEach((s) => (s.textContent = fmt(c[s.dataset.k], true)));
      $(".dur", el).textContent = `${(c.end - c.start).toFixed(0)} s`;
    };
    showT();
    $$(".nudge button", el).forEach((b) => (b.onclick = () => {
      const k = b.dataset.k, d = +b.dataset.d;
      c[k] = Math.max(0, Math.min(m.duration || 1e9, c[k] + d));
      if (c.end - c.start < 3) c[k] -= d;
      showT();
      player.seek(k === "start" ? c.start : Math.max(c.start, c.end - 3));
    }));
    $(".hook-input", el).oninput = (e) => (c.hook = e.target.value.toUpperCase());
    $(".title-input", el).oninput = (e) => (c.title = e.target.value);
    $(".approve input", el).onchange = (e) => {
      c.keep = e.target.checked;
      el.className = "card clip " + (c.keep ? "on" : "off");
      upd();
    };
  });
  const first = () => m.clips.find((c) => c.keep) || m.clips[0];
  const lsrc = $("#live-src");
  lsrc.addEventListener("loadedmetadata", () => (lsrc.currentTime = first()?.start || 0), { once: true });
  const live = attachLive($("#live"), lsrc, m.vod, () => [first()?.start || 0, first()?.end || 30], () => first()?.hook || "", $("#pv-note"));
  bindFraming(() => live.refresh());
  S.onStyleChange = () => live.refresh();
  $("#all-on").onclick = () => { m.clips.forEach((c) => (c.keep = true)); viewReview(); };
  $("#all-off").onclick = () => { m.clips.forEach((c) => (c.keep = false)); viewReview(); };
  $("#style-btn").onclick = () => openStyle(m.vod, m.clips.find((c) => c.keep)?.start || 60);
  $("#render").onclick = async () => {
    try {
      const res = await runJob(`Creando ${count()} Shorts…`, () => api("/api/moments/render", {
        dir: m.dir, clips: m.clips, preset: S.preset, style: S.style, framing: S.framing,
      }));
      viewMomentResults(res);
    } catch (e) { toast(e.message, true); }
  };
  upd();
}

function viewMomentResults(res) {
  S.view = "moment-results";
  const name = (S.moments.vod || "").split(/[\\/]/).pop();
  crumbs([{ t: "Inicio", go: viewHome }, { t: name, go: S.video ? viewVideo : viewHome }, { t: "Revisar clips", go: viewReview }, { t: "Shorts listos" }]);
  const ok = res.clips.filter((c) => c.video);
  $("#view").innerHTML = `
    <div class="row" style="justify-content:space-between;margin-bottom:18px">
      <div><h1>🎉 ${ok.length} Shorts listos</h1><p class="muted">${esc(res.note || "")} Listos para TikTok, Reels y Shorts.</p></div>
      <div class="row"><button class="btn" id="back">← Volver a revisar</button><button class="btn primary" id="open">📂 Abrir carpeta</button></div>
    </div>
    <div class="results">${res.clips.map((c, i) => c.video ? `
      <div class="card tight result">
        <video playsinline src="${mediaUrl(c.video)}" controls preload="metadata"></video>
        <div class="meta"><b>${esc(c.title || "Clip " + (i + 1))}</b>
          ${c.hook ? `<span class="badge accent">${esc(c.hook)}</span>` : ""}
          ${c.hashtags?.length ? `<div class="tags">${c.hashtags.map((h) => `<span>${esc(h)}</span>`).join("")}</div>` : ""}
          <button class="btn sm" data-copy="${i}">📋 Copiar título y hashtags</button>${editBtn(i)}${saveBtn(c.video)}</div>
      </div>` : `<div class="card tight"><b>${esc(c.title || "Clip")}</b><p class="muted">❌ ${esc(c.error)}</p></div>`).join("")}
    </div>`;
  $("#back").onclick = viewReview;
  $("#open").onclick = () => api("/api/open", { path: res.folder });
  $$("[data-copy]").forEach((b) => (b.onclick = () => {
    const c = res.clips[+b.dataset.copy];
    copy([c.title, (c.hashtags || []).join(" ")].filter(Boolean).join("\n\n"));
  }));
  $$("[data-edit]").forEach((b) => (b.onclick = () => {
    const c = res.clips[+b.dataset.edit];
    viewEdit(c.video, () => viewMomentResults(res), (nv) => (c.video = nv));
  }));
}

// ---------------------------------------------------------------- episodio: configurar
function viewEpisodeConfig() {
  S.view = "episode-config";
  const vd = S.video;
  crumbs([{ t: "Inicio", go: viewHome }, { t: vd.name, go: viewVideo }, { t: "Episodio de YouTube" }]);
  const maxMin = Math.max(5, Math.min(60, Math.floor(vd.duration / 60)));
  $("#view").innerHTML = `
    <div class="config">
      <div class="card stack">
        <div><h2>📺 Episodio de YouTube</h2>
          <p class="muted">Se queda con los mejores tramos del directo, en orden, les quita los silencios largos y los parte en capítulos. Antes de renderizar puedes cambiar todo.</p></div>
        <label class="field"><span>Duración aproximada del episodio: <b id="tm-val"></b></span>
          <input type="range" id="tm" min="5" max="${maxMin}" value="${Math.min(20, maxMin)}"></label>
        <div>
          <label class="switch"><span class="lbl"><b>Intro con el mejor momento</b><small>10-15 s del momentazo antes de empezar</small></span><span class="toggle"><input type="checkbox" id="intro" checked><i></i></span></label>
          <label class="switch"><span class="lbl"><b>Quitar silencios largos</b><small>Pausas de más de 1,5 s sin voz ni juego</small></span><span class="toggle"><input type="checkbox" id="cut" checked><i></i></span></label>
        </div>
        ${aiField()}
        <div class="actions"><button class="btn primary big" id="go">📺 Preparar episodio</button>
          <span class="muted small">Después revisas capítulos, título y miniatura.</span></div>
      </div>
      <div class="card stack">
        <h3>Qué vas a recibir</h3>
        <div class="note">🎞️ <span><b>episodio.mp4</b> en 16:9 (1080p)</span></div>
        <div class="note">📑 <span><b>Capítulos</b> desde 00:00 en la descripción (YouTube los detecta solo)</span></div>
        <div class="note">🖼️ <span><b>Miniatura</b> 1280×720 con tu mejor reacción y un texto</span></div>
        <div class="note">💬 <span><b>Subtítulos .srt</b> para subirlos a YouTube</span></div>
      </div>
    </div>`;
  const tm = $("#tm");
  const upd = () => ($("#tm-val").textContent = `${tm.value} min`);
  tm.oninput = upd;
  upd();
  bindAi();
  $("#go").onclick = async () => {
    try {
      const res = await runJob("Preparando el episodio…", () => api("/api/episode/plan", {
        path: vd.path, preset: S.preset, style: S.style, ai: S.ai,
        target_min: +tm.value, intro: $("#intro").checked, cut_silences: $("#cut").checked,
      }));
      S.episode = { dir: res.dir, plan: res.plan, done: {} };
      viewEpisode();
    } catch (e) { toast(e.message, true); }
  };
}

async function loadEpisode(dir) {
  try {
    S.episode = await api(url("/api/episode", { dir }));
    viewEpisode();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- episodio: editar
function viewEpisode() {
  S.view = "episode";
  const E = S.episode, P = E.plan;
  const name = P.src.split(/[\\/]/).pop();
  crumbs([{ t: "Inicio", go: viewHome }, { t: name, go: S.video ? viewVideo : viewHome }, { t: "Episodio de YouTube" }]);
  const total = () => P.chapters.filter((c) => c.keep).reduce((a, c) => a + c.end - c.start, 0) + (P.intro ? P.intro[1] - P.intro[0] : 0);
  $("#view").innerHTML = `
    <div class="episode">
      <div class="stack">
        <video playsinline class="big-player" id="ep-player" controls preload="none" poster="${frameUrl(P.src, P.thumb_t, 960)}"></video>
        <div class="summary-line" id="ep-sum"></div>
        <div class="card stack">
          <div class="row" style="justify-content:space-between"><h2>Capítulos</h2><span class="muted small">Haz clic en la imagen para verlo · desmarca lo que sobre</span></div>
          ${P.intro ? `<div class="chapter" id="intro-row"><input type="checkbox" checked id="intro-on"><img src="${frameUrl(P.src, P.intro[0] + 2, 240)}" data-s="${P.intro[0]}" data-e="${P.intro[1]}"><div><b>Intro · el mejor momento</b><div class="when">${fmt(P.intro[0])} – ${fmt(P.intro[1])} del directo · ${Math.round(P.intro[1] - P.intro[0])} s</div></div><span></span></div>` : ""}
          <div class="chapters" id="chapters"></div>
        </div>
      </div>
      <div class="stack">
        <div class="card stack">
          <label class="field"><span>Título del vídeo</span><input type="text" id="ep-title" value="${esc(P.title)}" maxlength="100"></label>
          <label class="field"><span>Texto de la miniatura</span><input type="text" id="ep-thumb" class="hook-input" value="${esc(P.thumb_text)}"></label>
          <img class="thumb-preview" id="thumb" alt="Miniatura">
          <div class="row"><button class="btn sm" id="thumb-here">🖼️ Usar el fotograma del reproductor</button></div>
          <label class="field"><span>Descripción</span><textarea id="ep-desc" rows="3">${esc(P.description)}</textarea></label>
          <label class="field"><span>Tags (separados por comas)</span><input type="text" id="ep-tags" value="${esc((P.tags || []).join(", "))}"></label>
        </div>
        <button class="btn primary big" id="ep-render">🎬 Renderizar episodio</button>
        ${E.done?.video ? `<button class="btn" id="ep-last">Ver el último render</button>` : ""}
        ${P.notes?.length ? `<div class="note small">ℹ️ ${P.notes.map(esc).join("<br>")}</div>` : ""}
      </div>
    </div>`;
  const player = $("#ep-player");
  let loaded = false, stopAt = null, playingRow = null;
  const playRange = (s, e, row) => {
    if (!loaded) { player.src = mediaUrl(P.src); loaded = true; }
    player.currentTime = s;
    stopAt = e;
    player.play();
    playingRow?.classList.remove("playing");
    playingRow = row;
    row?.classList.add("playing");
    player.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  player.ontimeupdate = () => { if (stopAt && player.currentTime >= stopAt) { player.pause(); stopAt = null; } };
  const sum = () => {
    const n = P.chapters.filter((c) => c.keep).length;
    $("#ep-sum").innerHTML = `<span><b>${n}</b> capítulos</span><span>≈ <b>${fmtDur(total())}</b> antes de quitar silencios</span><span>del directo de <b>${fmtDur(P.duration)}</b></span>`;
  };
  const list = $("#chapters");
  P.chapters.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "chapter" + (c.keep ? "" : " off");
    row.innerHTML = `<input type="checkbox" ${c.keep ? "checked" : ""}>
      <img src="${frameUrl(P.src, (c.start + c.end) / 2, 240)}" loading="lazy" title="Ver este capítulo">
      <div><input type="text" value="${esc(c.title)}" placeholder="Título del capítulo"><div class="when">${fmt(c.start)} – ${fmt(c.end)} del directo · ${fmtDur(c.end - c.start)}</div></div>
      <button class="btn sm ghost" title="Reproducir">▶</button>`;
    list.appendChild(row);
    $("input[type=checkbox]", row).onchange = (e) => { c.keep = e.target.checked; row.classList.toggle("off", !c.keep); sum(); };
    $("input[type=text]", row).oninput = (e) => (c.title = e.target.value);
    const play = () => playRange(c.start, c.end, row);
    $("img", row).onclick = play;
    $("button", row).onclick = play;
  });
  if (P.intro) {
    $("#intro-on").onchange = (e) => { P._intro = P._intro || P.intro; P.intro = e.target.checked ? P._intro : null; $("#intro-row").classList.toggle("off", !e.target.checked); sum(); };
    $("#intro-row img").onclick = () => playRange(P.intro[0], P.intro[1], $("#intro-row"));
  }
  sum();
  const refreshThumb = debounce(async () => {
    try {
      const r = await api("/api/episode/thumb", { dir: E.dir, t: P.thumb_t, text: P.thumb_text, preset: S.preset, style: S.style });
      $("#thumb").src = pngUrl(r.url);
    } catch (e) { toast(e.message, true); }
  }, 450);
  refreshThumb();
  $("#ep-title").oninput = (e) => (P.title = e.target.value);
  $("#ep-thumb").oninput = (e) => { P.thumb_text = e.target.value.toUpperCase(); refreshThumb(); };
  $("#ep-desc").oninput = (e) => (P.description = e.target.value);
  $("#ep-tags").oninput = (e) => (P.tags = e.target.value.split(",").map((t) => t.trim()).filter(Boolean));
  $("#thumb-here").onclick = () => {
    if (!loaded) return toast("Reproduce un capítulo y páralo en el fotograma que quieras.");
    P.thumb_t = player.currentTime;
    refreshThumb();
    toast(`Miniatura del segundo ${fmt(P.thumb_t)}`);
  };
  $("#ep-last")?.addEventListener("click", () => viewEpisodeResult({ ...E.done, description_text: E.description_text, folder: E.dir }));
  $("#ep-render").onclick = async () => {
    if (!P.chapters.some((c) => c.keep)) return toast("Marca al menos un capítulo.", true);
    player.pause();
    const plan = { ...P };
    delete plan._intro;
    try {
      const res = await runJob("Renderizando el episodio…", () => api("/api/episode/render", { dir: E.dir, plan, preset: S.preset, style: S.style }));
      E.done = res;
      E.description_text = res.description_text;
      viewEpisodeResult(res);
    } catch (e) { toast(e.message, true); }
  };
}

function viewEpisodeResult(res) {
  S.view = "episode-result";
  const P = S.episode.plan;
  crumbs([{ t: "Inicio", go: viewHome }, { t: P.src.split(/[\\/]/).pop(), go: S.video ? viewVideo : viewHome }, { t: "Episodio", go: viewEpisode }, { t: "Listo" }]);
  $("#view").innerHTML = `
    <div class="row" style="justify-content:space-between;margin-bottom:18px">
      <div><h1>🎉 Episodio listo</h1><p class="muted">${res.duration ? fmtDur(res.duration) + " · " : ""}Súbelo a YouTube con la miniatura, la descripción y los subtítulos.</p></div>
      <div class="row"><button class="btn" id="back">← Editar</button><button class="btn primary" id="open">📂 Abrir carpeta</button></div>
    </div>
    <div class="episode">
      <video playsinline class="big-player" src="${mediaUrl(res.video)}" controls preload="metadata"></video>
      <div class="stack">
        <img class="thumb-preview" src="${mediaUrl(res.thumbnail)}" alt="Miniatura">
        <label class="field"><span>Descripción con capítulos</span><textarea rows="12" id="desc">${esc(res.description_text || "")}</textarea></label>
        <button class="btn" id="copy">📋 Copiar descripción</button>${saveBtn(res.video)}
      </div>
    </div>`;
  $("#back").onclick = viewEpisode;
  $("#open").onclick = () => api("/api/open", { path: res.folder || res.video });
  $("#copy").onclick = () => copy($("#desc").value);
}

// ---------------------------------------------------------------- zooms a mano
/** Lista de zooms ({t, d, x, y, w}) con su cuadro sobre la vista previa `lp`. `v`: el vídeo que se
 *  pausa para elegir el segundo. */
function bindZooms(zooms, lp, v, getRange) {
  let cur = null;
  const list = $("#zlist");
  const edit = (z) => {
    cur = z;
    lp.editZoom(z, () => lp.setZooms(zooms));
    v.pause();
    v.currentTime = z.t + Math.min(0.5, z.d / 2);
    draw();
  };
  const draw = () => {
    zooms.sort((a, b) => a.t - b.t);
    list.innerHTML = zooms.map((z, i) => `
      <div class="zrow ${z === cur ? "on" : ""}" data-i="${i}">
        <span class="zt">⏱ ${fmt(z.t, true)}</span>
        <button class="btn sm" data-a="-0.5" title="Antes">−</button><button class="btn sm" data-a="0.5" title="Después">＋</button>
        <span class="small">dura <b>${z.d.toFixed(1).replace(".", ",")} s</b></span>
        <input type="range" min="0.5" max="5" step="0.1" value="${z.d}" data-d>
        <button class="btn sm ${z === cur ? "primary" : ""}" data-e>${z === cur ? "✓ Listo" : "✏️ Zona"}</button>
        <button class="btn sm ghost" data-x title="Quitar">🗑</button></div>`).join("");
    $$(".zrow", list).forEach((row) => {
      const z = zooms[+row.dataset.i];
      $$("[data-a]", row).forEach((b) => (b.onclick = () => {
        const [a, e] = getRange();
        z.t = Math.max(a, Math.min(e - 0.3, z.t + +b.dataset.a));
        lp.setZooms(zooms);
        edit(z);
      }));
      $("[data-d]", row).oninput = (e) => { z.d = +e.target.value; $("b", row).textContent = z.d.toFixed(1).replace(".", ",") + " s"; lp.setZooms(zooms); };
      $("[data-e]", row).onclick = () => {
        if (z === cur) { cur = null; lp.stopEdit(); v.currentTime = Math.max(getRange()[0], z.t - 0.6); draw(); }
        else edit(z);
      };
      $("[data-x]", row).onclick = () => {
        zooms.splice(zooms.indexOf(z), 1);
        if (z === cur) { cur = null; lp.stopEdit(); }
        lp.setZooms(zooms);
        draw();
      };
    });
  };
  $("#zadd").onclick = () => {
    const [a, e] = getRange();
    const t = Math.max(a, Math.min(e - 0.5, v.currentTime || a));
    const z = { t, d: 1.5, x: 0.25, y: 0.3, w: 0.5 };
    zooms.push(z);
    lp.setZooms(zooms);
    edit(z);
    toast("Mueve el cuadro a la zona que quieras ver en grande");
  };
  draw();
}

// ---------------------------------------------------------------- Short de un clip
function viewShort() {
  S.view = "short";
  const vd = S.video;
  crumbs([{ t: "Inicio", go: viewHome }, { t: vd.name, go: viewVideo }, { t: "Convertir en Short" }]);
  const r = { start: 0, end: vd.duration };
  $("#view").innerHTML = `
    <div class="config">
      <div class="card stack">
        <h2>✂️ Convertir en Short</h2>
        <video playsinline class="big-player" id="sp" src="${mediaUrl(vd.path)}" controls preload="metadata"></video>
        <div class="timing">
          <div class="tbox"><small>Empieza</small><span class="tv" id="ts"></span><div class="nudge"><button id="set-s">⤓ Aquí</button><button id="reset-s">Inicio</button></div></div>
          <div class="tbox"><small>Termina</small><span class="tv" id="te"></span><div class="nudge"><button id="set-e">⤓ Aquí</button><button id="reset-e">Final</button></div></div>
        </div>
        <p class="muted small">Pausa el vídeo donde quieras y pulsa «Aquí» para recortar. Si no tocas nada, se usa el vídeo entero.</p>
        <label class="field"><span>Gancho en pantalla (opcional)</span><input type="text" id="hook" class="hook-input" placeholder="NO PUEDE SER"></label>
        ${fxFields(S.style)}
        <p class="muted small">Después de crearlo podrás corregir subtítulos y gancho si algo sale mal.</p>
        <div class="actions"><button class="btn primary big" id="go">✂️ Crear Short</button></div>
      </div>
      <div class="card stack">
        <h3>Así quedará <span class="muted small">· dale a ▶ para verlo en movimiento</span></h3>
        <div id="live"></div>
        <p class="muted small" id="pv-note" style="text-align:center"></p>
        <div class="zooms"><b class="small">🔍 Zoom en un momento</b>
          <p class="muted small">Pausa el vídeo en el segundo que quieras y pulsa «Añadir zoom aquí». Mueve el cuadro sobre la vista previa y agrándalo o achícalo por la esquina: esa zona es la que se verá en grande.</p>
          <div id="zlist" class="zooms"></div>
          <button class="btn sm" id="zadd">＋ Añadir zoom aquí</button></div>
        <button class="btn sm ghost" id="exact">📸 Ver un fotograma exacto (render real)</button>
        <img class="style-preview" id="pv" alt="" style="display:none">
        ${framingField()}
        <button class="btn" id="style-btn">🎨 Subtítulos, colores y extras</button>
      </div>
    </div>`;
  const v = $("#sp");
  const show = () => { $("#ts").textContent = fmt(r.start, true); $("#te").textContent = fmt(r.end, true); };
  show();
  $("#set-s").onclick = () => { r.start = Math.min(v.currentTime, r.end - 1); show(); };
  $("#set-e").onclick = () => { r.end = Math.max(v.currentTime, r.start + 1); show(); };
  $("#reset-s").onclick = () => { r.start = 0; show(); };
  $("#reset-e").onclick = () => { r.end = vd.duration; show(); };
  const live = attachLive($("#live"), v, vd.path, () => [r.start, r.end], () => $("#hook").value, $("#pv-note"));
  const pv = () => live.refresh();
  const zooms = [];
  bindZooms(zooms, live.lp, v, () => [r.start, r.end]);
  bindFx($("#view .fx"), S.style);
  bindFraming(pv);
  $("#hook").oninput = pv;
  ["set-s", "set-e", "reset-s", "reset-e"].forEach((id) => $("#" + id).addEventListener("click", pv));
  S.onStyleChange = pv;
  $("#exact").onclick = async () => {
    $("#pv-note").textContent = "Renderizando un fotograma exacto…";
    try {
      const res = await api("/api/preview", { path: vd.path, t: Math.max(r.start, Math.min(v.currentTime || 1, vd.duration - 0.5)), preset: S.preset, style: S.style, framing: S.framing, hook: $("#hook").value });
      $("#pv").src = pngUrl(res.url);
      $("#pv").style.display = "block";
      $("#pv-note").textContent = "Fotograma exacto (abajo) · " + res.note;
    } catch (e) { $("#pv-note").textContent = e.message; }
  };
  $("#style-btn").onclick = () => openStyle(vd.path, v.currentTime || 1);
  $("#go").onclick = async () => {
    const trim = r.start > 0.05 || r.end < vd.duration - 0.05;
    try {
      const res = await runJob("Creando tu Short…", () => api("/api/short/render", {
        path: vd.path, preset: S.preset, style: S.style, framing: S.framing, hook: $("#hook").value,
        start: trim ? r.start : null, end: trim ? r.end : null, zooms,
      }));
      v.pause();
      showShortResult(res);
    } catch (e) { toast(e.message, true); }
  };
}

function showShortResult(res) {
  S.view = "short-result";
  crumbs([{ t: "Inicio", go: viewHome }, { t: S.video?.name || "Vídeo", go: S.video ? viewShort : viewHome }, { t: "Short listo" }]);
  $("#view").innerHTML = `
    <div class="row" style="justify-content:space-between;margin-bottom:18px">
      <div><h1>🎉 Short listo</h1><p class="muted">${esc(res.note || "")}</p></div>
      <div class="row"><button class="btn" id="back">← Volver</button><button class="btn primary" id="open">📂 Abrir carpeta</button></div>
    </div>
    <div class="results" style="max-width:360px"><div class="card tight result"><video playsinline src="${mediaUrl(res.video)}" controls autoplay></video><div class="meta">${editBtn(0)}${saveBtn(res.video)}</div></div></div>`;
  $("#back").onclick = viewShort;
  $("#open").onclick = () => api("/api/open", { path: res.folder });
  $("[data-edit]").onclick = () => viewEdit(res.video, () => showShortResult(res), (nv) => (res.video = nv));
}

// ---------------------------------------------------------------- cómo empieza el Short (gancho, sonido, entrada)
const HOOK_ANIMS = [["pop", "Pop (crece y rebota)"], ["golpe", "Golpe (se estampa)"], ["caida", "Caída desde arriba"],
  ["deslizar", "Entra deslizando"], ["temblor", "Temblor"], ["fijo", "Aparece sin más"]];
const INTROS = [["ninguno", "Sin efecto"], ["zoom", "Zoom de golpe"], ["flash", "Flash blanco"], ["fundido", "Fundido desde negro"],
  ["desenfoque", "Desenfoque → nítido"], ["sacudida", "Sacudida de cámara"], ["deslizar", "Sube desde abajo"]];
let SOUNDS = null;
async function loadSounds(force = false) {
  if (!SOUNDS || force) SOUNDS = (await api("/api/sfx")).sounds;
  return SOUNDS;
}
const opt = (list, cur) => list.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("");

/** Selectores de animación del gancho, sonido del gancho y efecto de entrada. `vals` = objeto con las
 *  claves hook.anim / hook.sound / intro.effect (S.style o los ajustes de un Short concreto). */
const fadeTxt = (v) => (+v ? (+v).toFixed(1).replace(".", ",") + " s" : "de golpe");
function fxFields(vals) {
  return `
    <div class="fx">
      <label class="field"><span>Cómo aparece el gancho</span><select data-fx="hook.anim">${opt(HOOK_ANIMS, vals["hook.anim"] || "pop")}</select></label>
      <label class="field"><span>Sonido con el gancho</span>
        <div class="row nowrap"><select data-fx="hook.sound" class="grow"><option>Cargando…</option></select>
          <button class="btn sm" data-fx-play title="Escuchar">▶</button>
          <label class="btn sm" title="Añadir un sonido tuyo (mp3, wav…)">＋<input type="file" accept="audio/*,.mp3,.wav,.ogg,.m4a" data-fx-up hidden></label></div></label>
      <label class="field"><span>Cuánto dura el sonido <b data-fx-sv>entero</b></span>
        <input type="range" min="0.1" max="5" step="0.1" value="5" data-fx-sd></label>
      <label class="field"><span>Desvanecer el gancho <b data-fx-fv>${fadeTxt(vals["hook.fade_out"] ?? 0.6)}</b></span>
        <input type="range" min="0" max="2" step="0.1" data-fx="hook.fade_out" value="${vals["hook.fade_out"] ?? 0.6}"></label>
      <label class="field"><span>Cómo entra el vídeo</span><select data-fx="intro.effect">${opt(INTROS, vals["intro.effect"] || "ninguno")}</select></label>
    </div>`;
}
async function bindFx(root, vals, onChange = () => {}) {
  const sel = $('[data-fx="hook.sound"]', root);
  const fill = async (force) => {
    const list = await loadSounds(force).catch(() => []);
    sel.innerHTML = opt([["", "El de siempre del juego"], ["ninguno", "Sin sonido"], ...list.map((s) => [s.file, s.label])], vals["hook.sound"] || "");
  };
  await fill();
  $$("[data-fx]", root).forEach((el) => (el.onchange = () => {
    vals[el.dataset.fx] = el.type === "range" ? +el.value : el.value;
    onChange();
  }));
  // Duración del sonido: la barra llega hasta lo que dura el elegido; a tope = entero (se guarda 0).
  const sd = $("[data-fx-sd]", root), sv = $("[data-fx-sv]", root);
  let known = false;  // «El de siempre» depende del juego: ahí no sabemos cuánto dura
  const secs = (v) => (+v).toFixed(1).replace(".", ",") + " s";
  const sdShow = () => { sv.textContent = +sd.value < +sd.max ? secs(sd.value) : known ? `entero (${secs(sd.max)})` : "entero"; };
  const sdFit = (keep) => {
    const d = (SOUNDS || []).find((s) => s.file === sel.value)?.dur;
    known = !!d;
    sd.disabled = sel.value === "ninguno";
    sd.max = d ? Math.max(0.2, Math.ceil(d * 10) / 10) : 5;
    const cur = keep ? +vals["hook.sound_dur"] || 0 : 0;
    if (!keep) vals["hook.sound_dur"] = 0;
    sd.value = cur > 0 && cur < +sd.max ? cur : sd.max;
    sdShow();
  };
  sdFit(true);
  sel.addEventListener("change", () => sdFit(false));
  sd.oninput = () => { vals["hook.sound_dur"] = +sd.value >= +sd.max ? 0 : +sd.value; sdShow(); onChange(); };
  const fr = $('[data-fx="hook.fade_out"]', root);
  if (fr) fr.oninput = () => { $("[data-fx-fv]", root).textContent = fadeTxt(fr.value); vals["hook.fade_out"] = +fr.value; onChange(); };
  let audio;
  $("[data-fx-play]", root).onclick = () => {
    const f = sel.value;
    if (!f || f === "ninguno") return toast(f ? "Sin sonido" : "Elige un sonido de la lista para escucharlo");
    audio?.pause();
    const a = (audio = new Audio(url(`/api/sfx/file/${encodeURIComponent(f)}`)));
    a.play().catch(() => {});
    const cut = +vals["hook.sound_dur"] || 0;
    if (cut > 0) setTimeout(() => a.pause(), cut * 1000);
  };
  $("[data-fx-up]", root).onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const r = await fetch(url("/api/sfx/upload", { name: f.name }), { method: "PUT", headers: { "X-Token": K }, body: f });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      vals["hook.sound"] = j.file;
      vals["hook.sound_dur"] = 0;
      await fill(true);
      sdFit(false);
      onChange();
      toast("Sonido añadido ✓");
    } catch (err) { toast(err.message, true); }
  };
}

// ---------------------------------------------------------------- corregir un Short ya hecho
const editBtn = (i) => `<button class="btn sm" data-edit="${i}">✏️ Corregir textos y efectos</button>`;

/** Editor de un Short: subtítulos, gancho, sonido y entrada; «Rehacer» lo vuelve a renderizar.
 *  onDone(nuevoVideo) se llama tras rehacerlo; back() vuelve a la vista anterior. */
async function viewEdit(video, back, onDone = () => {}) {
  let data;
  try { data = await api("/api/edit?" + new URLSearchParams({ video })); } catch (e) { return toast(e.message, true); }
  S.view = "edit";
  crumbs([{ t: "Inicio", go: viewHome }, { t: "Shorts listos", go: back }, { t: "Corregir" }]);
  const fx = { ...data.fx };
  $("#view").innerHTML = `
    <div class="row" style="justify-content:space-between;margin-bottom:14px">
      <div><h1>✏️ Corregir el Short</h1><p class="muted">Arregla lo que se oyó mal, cambia el gancho o cómo empieza, y rehazlo.</p></div>
      <button class="btn" id="ed-back">← Volver</button>
    </div>
    <div class="config edit">
      <div class="card stack">
        <video playsinline class="edit-player" id="ed-v" src="${mediaUrl(video)}" controls preload="metadata"></video>
        <div class="row">${saveBtn(video)}</div>
      </div>
      <div class="card stack">
        <h3>Gancho</h3>
        <input type="text" id="ed-hook" class="hook-input" value="${esc(data.hook)}" placeholder="Sin gancho (vacío)">
        ${fxFields(fx)}
        <h3 style="margin-top:8px">Subtítulos <span class="muted small">· ${data.phrases.length} frases</span></h3>
        <p class="muted small">Toca el tiempo para oír esa frase. <b>*palabra*</b> = palabra clave en color. Una frase vacía se borra.</p>
        <div class="sub-list" id="ed-subs">${data.phrases.length ? data.phrases.map((p, i) => `
          <div class="sub-row" data-i="${i}"><button class="t" data-t="${p.start}">${fmt(p.start, true)}</button>
            <input type="text" value="${esc(p.text)}" data-orig="${esc(p.text)}"></div>`).join("") : '<p class="muted small">Este Short no tiene subtítulos.</p>'}
        </div>
        <div class="actions"><button class="btn primary big" id="ed-go">🔁 Rehacer el Short con los cambios</button></div>
      </div>
    </div>`;
  const v = $("#ed-v");
  $("#ed-back").onclick = back;
  await bindFx($("#view .fx"), fx);
  const rows = $$("#ed-subs .sub-row");
  rows.forEach((r) => {
    const inp = $("input", r);
    $(".t", r).onclick = () => { v.currentTime = +$(".t", r).dataset.t; v.play(); };
    inp.oninput = () => r.classList.toggle("changed", inp.value !== inp.dataset.orig);
  });
  v.ontimeupdate = () => {
    const t = v.currentTime;
    rows.forEach((r, i) => r.classList.toggle("now", t >= data.phrases[i].start - 0.05 && (i + 1 >= data.phrases.length || t < data.phrases[i + 1].start - 0.05)));
  };
  $("#ed-go").onclick = async () => {
    v.pause();
    try {
      const res = await runJob("Rehaciendo el Short…", () => api("/api/edit/render", {
        video, hook: $("#ed-hook").value, fx, texts: rows.map((r) => $("input", r).value),
      }));
      toast("Short rehecho ✓ (el anterior se conserva)");
      onDone(res.video);
      viewEdit(res.video, back, onDone);
    } catch (e) { toast(e.message, true); }
  };
}

// ---------------------------------------------------------------- panel «Mi estilo»
const STYLE_FIELDS = {
  subs: [["frase", "Frase", "3-5 palabras, blanco con contorno"], ["resaltado", "Resaltado", "minúsculas, palabras clave en color"], ["karaoke", "Karaoke", "palabra a palabra, la que dices cambia de color"]],
  extras: [
    ["toggles.subtitles", "Subtítulos", "Lo que dices, en pantalla"],
    ["toggles.hook_title", "Título gancho", "El texto grande del principio"],
    ["toggles.highlight_keywords", "Resaltar palabras clave", "Las palabras importantes en otro color"],
    ["toggles.cut_silences", "Cortar silencios", "Quita las pausas dentro del clip"],
    ["toggles.auto_zoom", "Auto-zoom", "Zoom en gritos y palabras clave"],
    ["toggles.sfx", "Efectos de sonido", "Whoosh y boom en el gancho y los zooms"],
    ["toggles.progress_bar", "Barra de progreso", "Arriba del vídeo"],
    ["toggles.watermark", "Marca de agua", "Tu @ en pantalla"],
  ],
  dialects: [["latam", "🌎 Latinoamérica"], ["ve", "🇻🇪 Venezuela"], ["mx", "🇲🇽 México"], ["co", "🇨🇴 Colombia"], ["ar", "🇦🇷 Argentina"], ["cl", "🇨🇱 Chile"], ["pe", "🇵🇪 Perú"], ["es", "🇪🇸 España"]],
};
let stylePreviewSrc = null, stylePreviewT = 1;

function openStyle(previewPath, t) {
  stylePreviewSrc = previewPath || S.video?.path || null;
  stylePreviewT = t || 1;
  renderStyle();
  $("#style-drawer").classList.add("open");
  $("#scrim").classList.add("show");
}
function closeStyle() {
  $("#style-drawer").classList.remove("open");
  $("#scrim").classList.remove("show");
}
function renderStyle() {
  const st = S.style, pi = presetInfo();
  $("#style-sub").textContent = `Cómo salen tus Shorts de ${pi?.label || S.preset}. Se guarda para ese juego.`;
  const sw = ([k, l, d]) => `<label class="switch"><span class="lbl"><b>${l}</b><small>${d}</small></span><span class="toggle"><input type="checkbox" data-k="${k}" ${st[k] ? "checked" : ""}><i></i></span></label>`;
  $("#style-body").innerHTML = `
    ${stylePreviewSrc ? `<div><img class="style-preview" id="st-pv" alt=""><p class="muted small" id="st-note" style="text-align:center;margin-top:6px"></p></div>` : `<div class="note">Elige un vídeo para ver aquí la vista previa.</div>`}
    <div><h3>Tu marca</h3><label class="field"><span>Tu @ (marca de agua y miniaturas)</span><input type="text" data-k="handle" value="${esc(st.handle || "")}" placeholder="@tucanal"></label></div>
    <div><h3>Subtítulos</h3>
      <div class="seg" id="sub-style">${STYLE_FIELDS.subs.map(([v, l]) => `<button data-v="${v}" class="${st["subtitles.style"] === v ? "on" : ""}">${l}</button>`).join("")}</div>
      <p class="muted small" id="sub-desc" style="margin-top:6px"></p>
      <div class="row" style="margin-top:12px">
        <label class="field grow"><span>Tamaño <b id="fs-v">${st["subtitles.font_size"]}</b></span><input type="range" min="40" max="120" data-k="subtitles.font_size" value="${st["subtitles.font_size"]}"></label>
        <label class="field"><span>Texto</span><input type="color" data-k="subtitles.primary_color" value="${st["subtitles.primary_color"] || "#ffffff"}"></label>
        <label class="field"><span>Clave</span><input type="color" data-k="subtitles.highlight_color" value="${st["subtitles.highlight_color"] || "#ffe14d"}"></label>
      </div>
      <label class="field" style="margin-top:10px"><span>Mayúsculas</span>
        <div class="seg" id="case">${[["original", "Como lo digo"], ["mayusculas", "MAYÚSCULAS"], ["minusculas", "minúsculas"]].map(([v, l]) => `<button data-v="${v}" class="${(st["subtitles.case"] || "original") === v ? "on" : ""}">${l}</button>`).join("")}</div></label>
    </div>
    <div><h3>Gancho</h3>
      <div class="row">
        <label class="field grow"><span>Dura <b id="hd-v">${st["hook.duration"]} s</b> (0 = todo el clip)</span><input type="range" min="0" max="8" step="0.5" data-k="hook.duration" value="${st["hook.duration"]}"></label>
        <label class="field"><span>Relleno</span><input type="color" data-k="hook.fill" value="${st["hook.fill"] || "#ffffff"}"></label>
        <label class="field"><span>Borde</span><input type="color" data-k="hook.outline_color" value="${st["hook.outline_color"] || "#e3262e"}"></label>
      </div>
      <div id="st-fx">${fxFields(st)}</div></div>
    <div><h3>Extras</h3>${STYLE_FIELDS.extras.map(sw).join("")}</div>
    <div><h3>Cómo hablas</h3>
      <label class="field"><span>Acento (Whisper escribe mejor tu jerga)</span><select data-k="transcription.dialect">${STYLE_FIELDS.dialects.map(([v, l]) => `<option value="${v}" ${st["transcription.dialect"] === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="field" style="margin-top:10px"><span>Glosario: nombres del juego, amigos, muletillas</span><textarea rows="3" data-k="transcription.glossary" placeholder="Adrián, Christian, Conde, cashout, zipline…">${esc(st["transcription.glossary"] || "")}</textarea></label>
      <label class="field" style="margin-top:10px"><span>Pista del micro (0 = primera)</span><input type="number" min="0" max="5" data-k="audio.transcribe_track" value="${st["audio.transcribe_track"] ?? 0}" style="width:90px"></label>
    </div>
    <div class="row"><button class="btn primary grow" id="st-save">💾 Guardar como mi estilo de ${esc(pi?.label || S.preset)}</button></div>`;
  const preview = debounce(async () => {
    if (!stylePreviewSrc) return;
    $("#st-note").textContent = "Generando vista previa…";
    try {
      const res = await api("/api/preview", { path: stylePreviewSrc, t: stylePreviewT, preset: S.preset, style: S.style, framing: S.framing, hook: "QUÉ LOCURA" });
      $("#st-pv").src = pngUrl(res.url);
      $("#st-note").textContent = res.note;
    } catch (e) { $("#st-note").textContent = e.message; }
  }, 450);
  const changed = () => { preview(); S.onStyleChange?.(); };
  const subDesc = () => ($("#sub-desc").textContent = (STYLE_FIELDS.subs.find((s) => s[0] === st["subtitles.style"]) || [])[2] || "");
  subDesc();
  $$("#style-body [data-k]").forEach((el) => {
    const k = el.dataset.k;
    el.addEventListener(el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input", () => {
      st[k] = el.type === "checkbox" ? el.checked : el.type === "range" || el.type === "number" ? +el.value : el.value;
      if (k === "subtitles.font_size") $("#fs-v").textContent = el.value;
      if (k === "hook.duration") $("#hd-v").textContent = el.value + " s";
      changed();
    });
  });
  const seg = (id, key) => $$(`#${id} button`).forEach((b) => (b.onclick = () => {
    st[key] = b.dataset.v;
    $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b));
    if (key === "subtitles.style") subDesc();
    changed();
  }));
  seg("sub-style", "subtitles.style");
  seg("case", "subtitles.case");
  bindFx($("#st-fx"), st, () => S.onStyleChange?.());
  $("#st-save").onclick = async () => {
    try {
      await api(`/api/presets/${S.preset}`, st);
      const p = presetInfo();
      if (p) p.style = { ...st };
      toast("Estilo guardado ✓");
    } catch (e) { toast(e.message, true); }
  };
  preview();
}

// ---------------------------------------------------------------- arranque
function selectPreset(id) {
  S.preset = id;
  savePrefs();
  S.style = { ...(presetInfo()?.style || {}) };
}

async function init() {
  $("#go-home").onclick = viewHome;
  $("#open-style").onclick = () => openStyle();
  $("#close-style").onclick = closeStyle;
  $("#scrim").onclick = closeStyle;
  $("#open-output").onclick = () => api("/api/open", {}).catch((e) => toast(e.message, true));
  $("#open-phone").onclick = phonePanel;
  if (window.CF_PHONE) document.body.classList.add("phone");
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeStyle(); });
  try {
    const data = await api("/api/presets");
    S.presets = data.presets;
    S.aiKeys = data.ai_keys;
    Object.assign(S, await api("/api/prefs"));
  } catch (e) { toast("No se pudo cargar: " + e.message, true); }
  if (!S.presets.some((p) => p.id === S.preset)) S.preset = S.presets[0]?.id || "gta";
  selectPreset(S.preset);
  const sel = $("#preset-select");
  sel.innerHTML = S.presets.map((p) => `<option value="${p.id}" ${p.id === S.preset ? "selected" : ""}>${p.icon} ${esc(p.label)}</option>`).join("");
  sel.onchange = () => {
    selectPreset(sel.value);
    if ($("#style-drawer").classList.contains("open")) renderStyle();
    S.onStyleChange?.();
    toast(`Juego: ${presetInfo().label}`);
  };
  viewHome();
}
init();

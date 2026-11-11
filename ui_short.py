"""Vista previa en vivo del Short: reproduce el vídeo original con el layout aplicado
(dibujado en un <canvas>), con pausa / retroceder / adelantar, el trozo del clip
(inicio y fin arrastrables) y elementos que se colocan arrastrando:

- facecam (layout esquina): mover y cambiar el tamaño desde la esquina,
- línea del split, franja del gameplay en blur,
- subtítulo y título gancho (subir/bajar; + / − cambian el tamaño).

Es una aproximación rápida para colocar cosas; el render final lo hace FFmpeg igual que siempre.
Su valor es un JSON que genera app.short_preview_value(). Al soltar algo emite `input` con
{"edit": {...}} (ajustes que cambiaron), "trim": [inicio, fin] y "t" (segundo actual).
"""
from __future__ import annotations

import gradio as gr

from ui_theme import ACCENT, BORDER, MUTED, SURFACE_2, TEXT

TEMPLATE = '<div class="cf-sp"></div>'

CSS = f"""
.cf-sp {{ display: flex; flex-direction: column; gap: 10px; align-items: stretch; }}
.cf-sp-empty {{ border: 1.5px dashed #3a3a40; border-radius: 16px; padding: 60px 20px; text-align: center;
  color: {MUTED}; font-size: 15px; }}
.cf-sp-stage {{ position: relative; display: flex; justify-content: center; }}
.cf-sp canvas {{ display: block; max-width: 100%; max-height: 66vh; border-radius: 14px; background: #000;
  touch-action: none; cursor: default; }}
.cf-sp video {{ position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }}
.cf-sp-bar {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: center; }}
.cf-sp-bar button {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 999px;
  min-width: 40px; height: 38px; padding: 0 12px; font-size: 15px; font-weight: 700; cursor: pointer; }}
.cf-sp-bar button:hover {{ border-color: {ACCENT}; }}
.cf-sp-bar button.play {{ background: {ACCENT}; color: #0a0a0b; border-color: {ACCENT}; min-width: 52px; }}
.cf-sp-time {{ font-family: 'JetBrains Mono', Consolas, monospace; font-size: 13px; color: {MUTED}; min-width: 96px;
  text-align: center; }}
.cf-sp-tl {{ position: relative; height: 34px; margin: 2px 8px; cursor: pointer; user-select: none; touch-action: none; }}
.cf-sp-tl .rail {{ position: absolute; left: 0; right: 0; top: 13px; height: 8px; border-radius: 99px; background: #2a2a2e; }}
.cf-sp-tl .sel {{ position: absolute; top: 13px; height: 8px; background: rgba(212,255,58,.35); border-radius: 99px; }}
.cf-sp-tl .h {{ position: absolute; top: 4px; width: 14px; height: 26px; margin-left: -7px; border-radius: 6px;
  background: {ACCENT}; cursor: ew-resize; box-shadow: 0 0 0 2px #0a0a0b; }}
.cf-sp-tl .ph {{ position: absolute; top: 0; width: 3px; height: 34px; margin-left: -1.5px; background: #fff;
  border-radius: 2px; pointer-events: none; }}
.cf-sp-clip {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: center;
  font-size: 13px; color: {MUTED}; }}
.cf-sp-clip b {{ color: {TEXT}; font-weight: 700; }}
.cf-sp-clip button {{ background: transparent; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 999px;
  padding: 5px 12px; font-weight: 700; font-size: 13px; cursor: pointer; }}
.cf-sp-clip button:hover, .cf-sp-clip button.on {{ border-color: {ACCENT}; color: {ACCENT}; }}
.cf-sp-pad {{ display: flex; visibility: hidden; align-items: center; gap: 6px; justify-content: center;
  flex-wrap: wrap; font-size: 13px; color: {MUTED}; }}
.cf-sp-pad.show {{ visibility: visible; }}
.cf-sp-pad b {{ color: {ACCENT}; }}
.cf-sp-pad button {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 10px;
  width: 38px; height: 34px; font-size: 16px; font-weight: 800; cursor: pointer; }}
.cf-sp-pad button:hover {{ border-color: {ACCENT}; }}
.cf-sp-help {{ font-size: 13px; color: {MUTED}; text-align: center; line-height: 1.5; }}
"""

JS = r"""
const OW = 1080, OH = 1920, LW = 1920, LH = 1080;
let S = {}, sel = null, hover = null, drag = null, raf = 0, fontsDone = {};
let root, cv, ctx, video;
const $ = q => root.querySelector(q);

function setup() {
root = element.querySelector('.cf-sp');
root.innerHTML = `
  <div class="cf-sp-empty">Sube un vídeo para verlo aquí con el layout aplicado.</div>
  <div class="cf-sp-main" style="display:none">
    <div class="cf-sp-stage"><canvas></canvas><video playsinline preload="auto"></video></div>
    <div class="cf-sp-pad"><span>Mover <b class="nm"></b>:</span>
      <button data-n="l">◀</button><button data-n="u">▲</button><button data-n="d">▼</button><button data-n="r">▶</button>
      <span style="margin-left:6px">Tamaño:</span><button data-n="-">−</button><button data-n="+">+</button>
    </div>
    <div class="cf-sp-bar">
      <button data-a="start" title="Ir al inicio del clip">⏮</button>
      <button data-a="back" title="Retroceder 5 s">⏪ 5</button>
      <button data-a="frame-" title="Fotograma anterior">‹</button>
      <button data-a="play" class="play" title="Reproducir / pausa">▶</button>
      <button data-a="frame+" title="Fotograma siguiente">›</button>
      <button data-a="fwd" title="Adelantar 5 s">5 ⏩</button>
      <button data-a="mute" title="Sonido">🔊</button>
      <span class="cf-sp-time">0:00 / 0:00</span>
    </div>
    <div class="cf-sp-tl"><div class="rail"></div><div class="sel"></div>
      <div class="h" data-h="a" title="Inicio del clip"></div><div class="h" data-h="b" title="Fin del clip"></div>
      <div class="ph"></div></div>
    <div class="cf-sp-clip">Clip: <b class="dur">0:00</b> (<span class="rng"></span>)
      <button data-d="15">15 s</button><button data-d="30">30 s</button><button data-d="60">60 s</button>
      <button data-d="0">Todo</button><button data-d="here" title="El clip empieza donde está la reproducción">⇤ Empieza aquí</button>
    </div>
    <div class="cf-sp-help"></div>
  </div>`;
cv = root.querySelector('canvas'); ctx = cv.getContext('2d');
video = root.querySelector('video');
listen();
}

const fmt = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return m + ':' + String(s).padStart(2, '0'); };
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const trim = () => S.trim || [0, S.dur || 0];

function parse() { try { return JSON.parse(props.value || '{}') || {}; } catch (e) { return {}; } }

function emit(edit) {
  const out = Object.assign({}, S, {edit: edit || {}, t: video.currentTime});
  props.value = JSON.stringify(out);
  trigger('input');
}

function loadFonts() {
  for (const f of S.fonts || []) {
    const key = f.family + '|' + f.url;
    if (fontsDone[key]) continue;
    fontsDone[key] = 1;
    new FontFace(f.family, 'url(' + f.url + ')').load().then(ff => { document.fonts.add(ff); draw(); }).catch(() => {});
  }
}

function apply() {
  const r = element.querySelector('.cf-sp');
  if (r && (r !== root || !r.querySelector('canvas'))) setup();
  S = parse();
  const has = !!S.video;
  $('.cf-sp-empty').style.display = has ? 'none' : '';
  $('.cf-sp-main').style.display = has ? '' : 'none';
  if (!has) return;
  cv.width = S.short ? OW : LW;
  cv.height = S.short ? OH : LH;
  if (video.dataset.src !== S.video) {
    video.dataset.src = S.video;
    video.src = S.video;
    video.addEventListener('loadeddata', () => { video.currentTime = trim()[0]; }, {once: true});
  }
  loadFonts();
  $('.cf-sp-help').innerHTML = S.short
    ? 'Haz clic en la <b>facecam</b>, la <b>línea del split</b>, el <b>subtítulo</b> o el <b>gancho</b> y arrástralos. ' +
      'También puedes usar las flechas y + / −. Arrastra las asas verdes de la barra para elegir el trozo del clip.'
    : 'Arrastra las asas verdes de la barra para elegir el trozo del clip.';
  timeline();
  draw();
}

// ---------------------------------------------------------------- dibujo
function rr(x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); }

function cover(src, dst, filter) {
  // dibuja src (recorte del original) llenando dst, recortando lo que sobre
  const [sx, sy, sw, sh] = src, [dx, dy, dw, dh] = dst;
  const k = Math.max(dw / sw, dh / sh), cw = dw / k, ch = dh / k;
  ctx.save(); ctx.beginPath(); ctx.rect(dx, dy, dw, dh); ctx.clip();
  if (filter) ctx.filter = filter;
  const pad = filter ? 40 : 0;
  ctx.drawImage(video, sx + (sw - cw) / 2, sy + (sh - ch) / 2, cw, ch, dx - pad, dy - pad, dw + 2 * pad, dh + 2 * pad);
  ctx.restore();
}

function fit(src, dst) {
  const [sx, sy, sw, sh] = src, [dx, dy, dw, dh] = dst;
  const k = Math.min(dw / sw, dh / sh), w = sw * k, h = sh * k;
  ctx.drawImage(video, sx, sy, sw, sh, dx + (dw - w) / 2, dy + (dh - h) / 2, w, h);
}

function caseText(t, c) { return c === 'mayusculas' ? t.toUpperCase() : c === 'minusculas' ? t.toLowerCase() : t; }

function wrap(text, maxW) {
  const words = text.split(/\s+/), lines = [];
  let cur = '';
  for (const w of words) {
    const tryL = cur ? cur + ' ' + w : w;
    if (ctx.measureText(tryL).width > maxW && cur) { lines.push(cur); cur = w; } else cur = tryL;
  }
  if (cur) lines.push(cur);
  return lines;
}

function textBlock(lines, cx, cy, size, layers, fill, shadow) {
  const lh = size * 1.12, top = cy - (lines.length - 1) * lh / 2;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
  lines.forEach((ln, i) => {
    const y = top + i * lh;
    if (shadow) { ctx.save(); ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillText(ln, cx + shadow, y + shadow); ctx.restore(); }
    for (const [w, c] of layers) if (w > 0) { ctx.lineWidth = w; ctx.strokeStyle = c; ctx.strokeText(ln, cx, y); }
    ctx.fillStyle = fill; ctx.fillText(ln, cx, y);
  });
  const width = Math.max(...lines.map(l => ctx.measureText(l).width), 10);
  return [cx - width / 2 - 12, top - lh / 2 - 6, width + 24, lines.length * lh + 12];
}

const boxes = {};

function drawSubs(rel) {
  const s = S.sub;
  if (!s || !s.show) { delete boxes.sub; return; }
  ctx.font = `900 ${s.size}px "${s.font}", "Arial Black", sans-serif`;
  const words = s.phrases[Math.floor(rel / 1.2) % s.phrases.length] || '';
  const text = caseText(words, s.case);
  const lines = wrap(text, s.maxW);
  const hl = s.highlight ? s.hlWord : null;
  boxes.sub = textBlock(lines, s.x, s.y, s.size, [[s.outline * 2, s.outlineColor]], s.color, s.shadow);
  if (hl) { /* el resaltado exacto se ve en el fotograma exacto */ }
}

function drawHook(rel) {
  const h = S.hook;
  if (!h || !h.show || !(h.dur === 0 || rel <= h.dur || sel === 'hook' || video.paused)) { delete boxes.hook; return; }
  ctx.save();
  ctx.font = `italic 900 ${h.size}px "${h.font}", "Arial Black", sans-serif`;
  const lines = h.text.split('|').map(x => x.trim()).filter(Boolean);
  ctx.translate(OW / 2, h.y); ctx.rotate(-h.tilt * Math.PI / 180); ctx.translate(-OW / 2, -h.y);
  boxes.hook = textBlock(lines, OW / 2, h.y, h.size, [[(h.outline + h.outer) * 2, h.outerColor], [h.outline * 2, h.outlineColor]], h.fill, 0);
  ctx.restore();
}

function draw() {
  if (!S.video || !cv.width) return;
  const W = cv.width, H = cv.height;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
  if (video.readyState < 2) { ctx.fillStyle = '#888'; ctx.font = '48px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('Cargando vídeo…', W / 2, H / 2); return; }
  const [a, b] = trim(), rel = video.currentTime - a;
  for (const k in boxes) delete boxes[k];
  if (!S.short) {
    ctx.drawImage(video, 0, 0, W, H);
  } else {
    if (S.type === 'blur') {
      cover(S.bg, [0, 0, OW, OH], `blur(${Math.max(1, S.blur)}px) brightness(0.94)`);
      ctx.drawImage(video, ...S.gp, ...S.gpDst);
      boxes.band = S.gpDst;
    } else if (S.fit) {
      cover(S.gp, S.gpDst, `blur(${Math.max(1, S.blur)}px) brightness(0.94)`);
      fit(S.gp, S.gpDst);
    } else {
      cover(S.gp, S.gpDst);
    }
    if (S.type === 'split' && S.fcSrc) {
      cover(S.fcSrc, S.fcDst);
      boxes.split = [0, S.fcDst[3] - 30, OW, 60];
    } else if (S.fcSrc && S.fcDst) {
      const [x, y, w, h] = S.fcDst;
      ctx.save();
      if (S.shadow) { ctx.shadowColor = 'rgba(0,0,0,.6)'; ctx.shadowBlur = 32; ctx.shadowOffsetY = 10;
        rr(x, y, w, h, S.radius); ctx.fillStyle = '#000'; ctx.fill(); }
      ctx.restore(); ctx.save(); rr(x, y, w, h, S.radius); ctx.clip();
      cover(S.fcSrc, S.fcDst); ctx.restore();
      boxes.fc = S.fcDst;
    }
    if (S.pb) {
      const p = clamp(rel / Math.max(0.1, b - a), 0, 1), y = S.pb.pos === 'abajo' ? OH - S.pb.h : 0;
      ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.fillRect(0, y, OW, S.pb.h);
      ctx.fillStyle = S.pb.color; ctx.fillRect(0, y, OW * p, S.pb.h);
    }
    drawHook(rel);
  }
  drawSubs(rel);
  // contorno de lo que se puede mover
  for (const k of [hover, sel]) {
    if (!k || !boxes[k]) continue;
    const [x, y, w, h] = boxes[k];
    ctx.save(); ctx.setLineDash(k === sel ? [] : [14, 10]); ctx.lineWidth = 6; ctx.strokeStyle = '#d4ff3a';
    ctx.strokeRect(x, y, w, h);
    if (k === 'fc' && k === sel) { ctx.fillStyle = '#d4ff3a'; ctx.fillRect(x + w - 28, y + h - 28, 40, 40); }
    ctx.restore();
  }
  timeline();
}

function loop() { draw(); if (!video.paused) raf = requestAnimationFrame(loop); }

// ---------------------------------------------------------------- reproducción y clip
function timeline() {
  const d = S.dur || 1, [a, b] = trim(), t = video.currentTime || 0;
  $('.cf-sp-tl .sel').style.left = (a / d * 100) + '%';
  $('.cf-sp-tl .sel').style.width = ((b - a) / d * 100) + '%';
  $('.cf-sp-tl .h[data-h=a]').style.left = (a / d * 100) + '%';
  $('.cf-sp-tl .h[data-h=b]').style.left = (b / d * 100) + '%';
  $('.cf-sp-tl .ph').style.left = (t / d * 100) + '%';
  $('.cf-sp-time').textContent = fmt(t) + ' / ' + fmt(d);
  $('.cf-sp-clip .dur').textContent = fmt(b - a);
  $('.cf-sp-clip .rng').textContent = fmt(a) + ' → ' + fmt(b);
  $('.cf-sp-bar .play').textContent = video.paused ? '▶' : '⏸';
  const full = a <= 0.05 && b >= d - 0.05;
  root.querySelectorAll('.cf-sp-clip button[data-d]').forEach(btn => {
    const v = btn.dataset.d;
    btn.classList.toggle('on', v === '0' ? full : (v !== 'here' && !full && Math.abs((b - a) - Number(v)) < 0.3));
  });
}

function seek(t) { const [a, b] = trim(); video.currentTime = clamp(t, 0, S.dur || 0); draw(); }

function setTrim(a, b, emitNow = true) {
  const d = S.dur || 0;
  a = clamp(a, 0, d); b = clamp(b, 0, d);
  if (b - a < 1) b = Math.min(d, a + 1);
  S.trim = [Math.round(a * 100) / 100, Math.round(b * 100) / 100];
  timeline();
  if (emitNow) emit({});
}

function listen() {
video.addEventListener('timeupdate', () => {
  const [a, b] = trim();
  if (!video.paused && video.currentTime >= b) { video.currentTime = a; }
  timeline();
});
video.addEventListener('seeked', draw);
video.addEventListener('loadeddata', draw);
video.addEventListener('play', () => { cancelAnimationFrame(raf); loop(); });
video.addEventListener('pause', () => { draw(); });

root.querySelector('.cf-sp-bar').addEventListener('click', e => {
  const btn = e.target.closest('button'); if (!btn) return;
  const [a, b] = trim(), act = btn.dataset.a, f = 1 / (S.fps || 30);
  if (act === 'play') {
    if (video.paused) { if (video.currentTime < a || video.currentTime >= b - 0.05) video.currentTime = a; video.play(); }
    else video.pause();
  }
  if (act === 'start') seek(a);
  if (act === 'back') seek(video.currentTime - 5);
  if (act === 'fwd') seek(video.currentTime + 5);
  if (act === 'frame-') { video.pause(); seek(video.currentTime - f); }
  if (act === 'frame+') { video.pause(); seek(video.currentTime + f); }
  if (act === 'mute') { video.muted = !video.muted; btn.textContent = video.muted ? '🔇' : '🔊'; }
  timeline();
});

root.querySelector('.cf-sp-clip').addEventListener('click', e => {
  const btn = e.target.closest('button'); if (!btn) return;
  const d = S.dur || 0, v = btn.dataset.d, [a] = trim();
  if (v === '0') setTrim(0, d);
  else if (v === 'here') setTrim(video.currentTime, Math.min(d, video.currentTime + (trim()[1] - a)));
  else { const n = Number(v); const start = a + n <= d ? a : Math.max(0, d - n); setTrim(start, start + n); }
  seek(trim()[0]);
});

const tl = root.querySelector('.cf-sp-tl');
tl.addEventListener('pointerdown', e => {
  const r = tl.getBoundingClientRect(), d = S.dur || 0;
  const at = ev => clamp((ev.clientX - r.left) / r.width, 0, 1) * d;
  const h = e.target.dataset.h;
  tl.setPointerCapture(e.pointerId);
  const move = ev => {
    const [a, b] = trim(), t = at(ev);
    if (h === 'a') setTrim(Math.min(t, b - 1), b, false);
    else if (h === 'b') setTrim(a, Math.max(t, a + 1), false);
    else seek(t);
    if (h) seek(h === 'a' ? trim()[0] : trim()[1]);
  };
  const up = () => { tl.removeEventListener('pointermove', move); tl.removeEventListener('pointerup', up);
    if (h) emit({}); };
  move(e);
  tl.addEventListener('pointermove', move); tl.addEventListener('pointerup', up);
});
listenCanvas();
}

// ---------------------------------------------------------------- arrastrar elementos
const NAMES = {fc: 'facecam', split: 'línea del split', band: 'gameplay', sub: 'subtítulo', hook: 'gancho'};

function pt(e) { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; }
function hit(x, y) {
  for (const k of ['hook', 'sub', 'fc', 'split', 'band']) {
    const bx = boxes[k]; if (bx && x >= bx[0] && x <= bx[0] + bx[2] && y >= bx[1] && y <= bx[1] + bx[3]) return k;
  }
  return null;
}
function setSel(k) {
  sel = k;
  const pad = $('.cf-sp-pad');
  pad.classList.toggle('show', !!k);
  if (k) pad.querySelector('.nm').textContent = NAMES[k];
  draw();
}

// Cambia la posición (dx, dy) y/o el tamaño (ds, en px o pasos) del elemento k, y devuelve los ajustes.
function change(k, dx, dy, ds) {
  if (k === 'fc') {
    let [x, y, w, h] = S.fcDst;
    if (ds) { const nw = clamp(w + ds, OW * 0.2, OW); h = h * nw / w; w = nw; }
    x = clamp(x + dx, 0, OW - w); y = clamp(y + dy, 0, OH - h);
    S.fcDst = [x, y, w, h];
    return {'layout.facecam_position': 'custom', 'layout.facecam_x': Math.round(x), 'layout.facecam_y': Math.round(y),
            'layout.facecam_width': Math.round(w / OW * 1000) / 10};
  }
  if (k === 'split') {
    const top = clamp(S.fcDst[3] + dy + ds, OH * 0.15, OH * 0.7);
    S.fcDst = [0, 0, OW, top]; S.gpDst = [0, top, OW, OH - top];
    return {'layout.split_ratio': Math.round(top / OH * 1000) / 10};
  }
  if (k === 'band') {
    const [x, y, w, h] = S.gpDst, ny = clamp(y + dy, 0, OH - h);
    S.gpDst = [x, ny, w, h];
    return {'layout.blur_fg_y': Math.round(ny + h / 2)};
  }
  if (k === 'sub') {
    S.sub.y = clamp(S.sub.y + dy, 100, OH - 300); S.sub.size = clamp(S.sub.size + ds, 30, 160);
    return {'subtitles.pos_y': Math.round(S.sub.y), 'subtitles.font_size': Math.round(S.sub.size)};
  }
  if (k === 'hook') {
    S.hook.y = clamp(S.hook.y + dy, 80, OH - 200); S.hook.size = clamp(S.hook.size + ds, 50, 220);
    return {'hook.pos_y': Math.round(S.hook.y), 'hook.font_size': Math.round(S.hook.size)};
  }
  return {};
}

function listenCanvas() {
cv.addEventListener('pointermove', e => {
  if (drag || !S.short) return;
  const [x, y] = pt(e), k = hit(x, y);
  if (k !== hover) { hover = k; cv.style.cursor = k ? (k === 'split' ? 'ns-resize' : 'move') : 'default'; if (video.paused) draw(); }
});
cv.addEventListener('pointerleave', () => { hover = null; if (video.paused) draw(); });
cv.addEventListener('pointerdown', e => {
  if (!S.short) return;
  const [x0, y0] = pt(e), k = hit(x0, y0);
  setSel(k);
  if (!k) return;
  e.preventDefault();
  cv.setPointerCapture(e.pointerId);
  const bx = boxes[k], resize = k === 'fc' && x0 > bx[0] + bx[2] - 50 && y0 > bx[1] + bx[3] - 50;
  let last = [x0, y0], edit = {};
  drag = k;
  const move = ev => {
    const [x, y] = pt(ev), dx = x - last[0], dy = y - last[1];
    last = [x, y];
    edit = Object.assign(edit, resize ? change(k, 0, 0, dx) : change(k, dx, dy, 0));
    draw();
  };
  const up = () => { cv.removeEventListener('pointermove', move); cv.removeEventListener('pointerup', up);
    drag = null; if (Object.keys(edit).length) emit(edit); };
  cv.addEventListener('pointermove', move); cv.addEventListener('pointerup', up);
});
$('.cf-sp-pad').addEventListener('click', e => { const b = e.target.closest('button'); if (b) nudge(b.dataset.n); });
root.tabIndex = 0;
root.addEventListener('keydown', e => {
  const k = {ArrowLeft: 'l', ArrowRight: 'r', ArrowUp: 'u', ArrowDown: 'd', '-': '-', '+': '+', '=': '+'}[e.key];
  if (k && sel) { e.preventDefault(); nudge(k); }
  if (e.key === ' ') { e.preventDefault(); root.querySelector('.cf-sp-bar .play').click(); }
});
}

let nudgeTimer = 0, nudgeEdit = {};
function nudge(n) {
  if (!sel) return;
  const step = 10;
  const m = {l: [-step, 0, 0], r: [step, 0, 0], u: [0, -step, 0], d: [0, step, 0], '-': [0, 0, -1], '+': [0, 0, 1]}[n];
  if (!m) return;
  const ds = m[2] * (sel === 'fc' ? 20 : sel === 'split' ? 20 : 4);
  Object.assign(nudgeEdit, change(sel, m[0], m[1], ds));
  draw();
  clearTimeout(nudgeTimer);
  nudgeTimer = setTimeout(() => { emit(nudgeEdit); nudgeEdit = {}; }, 450);
}

setup();
watch('value', apply);
apply();
"""


def short_preview(**kw) -> gr.HTML:
    return gr.HTML("{}", html_template=TEMPLATE, css_template=CSS, js_on_load=JS, **kw)

"""Editor visual del encuadre: arrastra y redimensiona la facecam (rojo) y el gameplay (azul)
directamente sobre un fotograma del vídeo original.

Es un gr.HTML con JavaScript propio. Su valor es un JSON:
    {"src": data-URL del fotograma, "W": ancho, "H": alto,
     "fc": [x, y, w, h] | null, "gp": [x, y, w, h] | null, "aspect": ancho/alto del hueco del gameplay}
(recuadros en fracciones del original). Al soltar un recuadro emite el evento `input`.
"""
from __future__ import annotations

import gradio as gr

from ui_theme import ACCENT, BORDER, MUTED, SURFACE_2, TEXT

FC_COLOR = "#ff4d5e"
GP_COLOR = "#3aa0ff"

TEMPLATE = '<div class="cf-ed"></div>'

CSS = f"""
.cf-ed {{ display: flex; flex-direction: column; gap: 10px; }}
.cf-ed-empty {{ border: 1.5px dashed #3a3a40; border-radius: 16px; padding: 48px 20px; text-align: center;
  color: {MUTED}; font-size: 15px; }}
.cf-ed-stage {{ position: relative; width: 100%; border-radius: 12px; overflow: hidden; user-select: none;
  touch-action: none; background: #000; }}
.cf-ed-stage img {{ display: block; width: 100%; height: auto; pointer-events: none; }}
.cf-ed-stage.dim::after {{ content: ""; position: absolute; inset: 0; background: rgba(0,0,0,.25); pointer-events: none; }}
.cf-box {{ position: absolute; box-sizing: border-box; border: 3px solid; border-radius: 6px; cursor: move; z-index: 2; }}
.cf-box.cf-gp {{ border-color: {GP_COLOR}; background: rgba(58,160,255,.10); }}
.cf-box.cf-fc {{ border-color: {FC_COLOR}; background: rgba(255,77,94,.12); z-index: 3; }}
.cf-box.active {{ box-shadow: 0 0 0 9999px rgba(0,0,0,.35); }}
.cf-tag {{ position: absolute; left: 6px; top: 6px; padding: 3px 9px; border-radius: 999px; font-size: 12px;
  font-weight: 700; color: #fff; white-space: nowrap; pointer-events: none; }}
.cf-gp .cf-tag {{ background: {GP_COLOR}; }}
.cf-fc .cf-tag {{ background: {FC_COLOR}; }}
.cf-box i {{ position: absolute; width: 16px; height: 16px; background: #fff; border-radius: 4px;
  border: 2px solid #111; }}
.cf-box i[data-h=nw] {{ left: -9px; top: -9px; cursor: nwse-resize; }}
.cf-box i[data-h=ne] {{ right: -9px; top: -9px; cursor: nesw-resize; }}
.cf-box i[data-h=sw] {{ left: -9px; bottom: -9px; cursor: nesw-resize; }}
.cf-box i[data-h=se] {{ right: -9px; bottom: -9px; cursor: nwse-resize; }}
.cf-ed-tools {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
.cf-ed-tools button {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 999px;
  padding: 8px 14px; font-weight: 600; font-size: 14px; cursor: pointer; }}
.cf-ed-tools button:hover {{ border-color: {ACCENT}; }}
.cf-ed-fit {{ margin-left: auto; font-size: 13px; color: {MUTED}; }}
.cf-ed-fit.ok {{ color: {ACCENT}; }}
.cf-ed-help {{ font-size: 13px; color: {MUTED}; line-height: 1.5; }}
.cf-ed-help b.fc {{ color: {FC_COLOR}; }}
.cf-ed-help b.gp {{ color: {GP_COLOR}; }}
"""

JS = r"""
const MIN = 0.03, TOL = 0.03;
let state = {};
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const round = r => r.map(v => Math.round(v * 10000) / 10000);

function parse() {
  try { return JSON.parse(props.value || '{}') || {}; } catch (e) { return {}; }
}
function emit() {
  props.value = JSON.stringify(state);
  trigger('input');
}
// Relación ancho/alto del recuadro azul medida en píxeles del vídeo.
function gpAspect(r) { return (r[2] * state.W) / (r[3] * state.H); }
function fills(r) { return state.aspect && Math.abs(gpAspect(r) / state.aspect - 1) <= TOL; }

function fitLabel(root) {
  const el = root.querySelector('.cf-ed-fit');
  if (!el || !state.gp) return;
  const ok = fills(state.gp);
  el.className = 'cf-ed-fit' + (ok ? ' ok' : '');
  el.textContent = ok ? '✓ Llena la pantalla' : '◐ El resto se rellena con el juego desenfocado';
}

function place(b, r) {
  b.style.left = (r[0] * 100) + '%';
  b.style.top = (r[1] * 100) + '%';
  b.style.width = (r[2] * 100) + '%';
  b.style.height = (r[3] * 100) + '%';
}

function drag(ev, b, key, stage, root) {
  ev.preventDefault();
  ev.stopPropagation();
  const h = ev.target.dataset.h || '';
  const rect = stage.getBoundingClientRect();
  const r0 = state[key].slice();
  const x0 = ev.clientX, y0 = ev.clientY;
  b.setPointerCapture(ev.pointerId);
  b.classList.add('active');
  const move = e => {
    const dx = (e.clientX - x0) / rect.width, dy = (e.clientY - y0) / rect.height;
    let [x, y, w, hh] = r0;
    if (!h) {
      x = clamp(x + dx, 0, 1 - w);
      y = clamp(y + dy, 0, 1 - hh);
    } else {
      let l = x, t = y, r = x + w, bt = y + hh;
      if (h.includes('w')) l = clamp(l + dx, 0, r - MIN);
      if (h.includes('e')) r = clamp(r + dx, l + MIN, 1);
      if (h.includes('n')) t = clamp(t + dy, 0, bt - MIN);
      if (h.includes('s')) bt = clamp(bt + dy, t + MIN, 1);
      x = l; y = t; w = r - l; hh = bt - t;
    }
    state[key] = round([x, y, w, hh]);
    place(b, state[key]);
    if (key === 'gp') fitLabel(root);
  };
  const up = () => {
    b.removeEventListener('pointermove', move);
    b.removeEventListener('pointerup', up);
    b.removeEventListener('pointercancel', up);
    b.classList.remove('active');
    emit();
  };
  b.addEventListener('pointermove', move);
  b.addEventListener('pointerup', up);
  b.addEventListener('pointercancel', up);
}

function addBox(stage, root, key, label) {
  const b = document.createElement('div');
  b.className = 'cf-box cf-' + key;
  b.innerHTML = '<span class="cf-tag">' + label + '</span>' +
    ['nw', 'ne', 'sw', 'se'].map(c => '<i data-h="' + c + '"></i>').join('');
  place(b, state[key]);
  b.addEventListener('pointerdown', ev => drag(ev, b, key, stage, root));
  stage.appendChild(b);
}

// Recuadro azul con la forma exacta del hueco, centrado en el actual y lo más grande posible dentro.
function snapFill() {
  const [x, y, w, h] = state.gp;
  const A = state.aspect * state.H / state.W;  // relación en fracciones
  let nw = w, nh = w / A;
  if (nh > h) { nh = h; nw = h * A; }
  if (nw > 1) { nw = 1; nh = nw / A; }
  if (nh > 1) { nh = 1; nw = nh * A; }
  const cx = x + w / 2, cy = y + h / 2;
  state.gp = round([clamp(cx - nw / 2, 0, 1 - nw), clamp(cy - nh / 2, 0, 1 - nh), nw, nh]);
}

function render() {
  state = parse();
  draw();
}

function draw() {
  const root = element.querySelector('.cf-ed');
  if (!root) return;
  if (!state.src) {
    root.innerHTML = '<div class="cf-ed-empty">Sube un vídeo y aquí aparecerá un fotograma para colocar ' +
      'la facecam y el gameplay arrastrando los recuadros.</div>';
    return;
  }
  const tools = [];
  if (state.gp) {
    tools.push('<button data-act="fill">⤢ Llenar la pantalla</button>');
    tools.push('<button data-act="all">▣ Ver todo el juego</button>');
  }
  const help = [];
  if (state.fc) help.push('<b class="fc">Rojo</b>: tu webcam en el vídeo original.');
  if (state.gp) help.push('<b class="gp">Azul</b>: la parte del juego que se verá.');
  if (help.length) help.push('Arrastra para mover y usa las esquinas para cambiar el tamaño.');
  root.innerHTML = '<div class="cf-ed-stage' + (state.gp || state.fc ? ' dim' : '') + '">' +
    '<img src="' + state.src + '" draggable="false"/></div>' +
    (tools.length ? '<div class="cf-ed-tools">' + tools.join('') + '<span class="cf-ed-fit"></span></div>' : '') +
    (help.length ? '<div class="cf-ed-help">' + help.join(' ') + '</div>' : '');
  const stage = root.querySelector('.cf-ed-stage');
  if (state.gp) addBox(stage, root, 'gp', 'Gameplay');
  if (state.fc) addBox(stage, root, 'fc', 'Facecam');
  fitLabel(root);
  root.querySelectorAll('.cf-ed-tools button').forEach(btn => btn.addEventListener('click', () => {
    if (btn.dataset.act === 'fill') snapFill();
    if (btn.dataset.act === 'all') state.gp = [0, 0, 1, 1];
    emit();
    draw();
  }));
}

watch('value', render);
render();
"""


def editor(**kw) -> gr.HTML:
    return gr.HTML("{}", html_template=TEMPLATE, css_template=CSS, js_on_load=JS, **kw)

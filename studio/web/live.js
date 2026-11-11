/* ClipForge Studio — vista previa en vivo del Short.
 * Dibuja el vídeo original en un <canvas> 9:16 con el layout que calcula el servidor
 * (/api/layout): se reproduce al instante, sin renderizar ni descargar nada.
 * Es una aproximación fiel en posiciones y tamaños; el render final lo hace FFmpeg. */
"use strict";

(function () {
  const W = 1080, H = 1920;
  const FONT_HOOK = "CF Montserrat", FONT_SUBS = "CF Nunito";
  const SAMPLE = [["qué", "locura", "de"], ["partida", "chicos"], ["mira", "esta", "jugada"]];

  function injectFonts(k) {
    if (document.getElementById("cf-fonts")) return;
    const st = document.createElement("style");
    st.id = "cf-fonts";
    st.textContent = `
      @font-face { font-family: "${FONT_HOOK}"; src: url("/api/font/Montserrat-Black.ttf?k=${encodeURIComponent(k)}"); }
      @font-face { font-family: "${FONT_SUBS}"; src: url("/api/font/Nunito-Black.ttf?k=${encodeURIComponent(k)}"); }`;
    document.head.appendChild(st);
    document.fonts?.load(`40px "${FONT_HOOK}"`);
    document.fonts?.load(`40px "${FONT_SUBS}"`);
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function wrap(ctx, text, maxW) {
    const lines = [];
    for (const word of text.split(/\s+/).filter(Boolean)) {
      const tryL = lines.length ? lines[lines.length - 1] + " " + word : word;
      if (lines.length && ctx.measureText(tryL).width <= maxW) lines[lines.length - 1] = tryL;
      else lines.push(word);
    }
    return lines;
  }

  function interp(points, t) {
    if (!points?.length) return null;
    if (t <= points[0][0]) return points[0][1];
    for (let i = 1; i < points.length; i++) {
      if (t <= points[i][0]) {
        const [t0, x0] = points[i - 1], [t1, x1] = points[i];
        return x0 + (x1 - x0) * ((t - t0) / Math.max(1e-6, t1 - t0));
      }
    }
    return points[points.length - 1][1];
  }

  class LivePreview {
    /** container: elemento donde va el lienzo. video: <video> con el original (se reutiliza si se da). */
    constructor(container, video, token) {
      injectFonts(token);
      this.video = video;
      this.g = null;
      this.track = null;
      this.range = null; // [inicio, fin]: reproducir en bucle ese tramo
      this.zooms = []; // zooms a mano: {t, d, x, y, w} (t en segundos del vídeo; x, y, w fracciones del Short)
      this.editing = null; // el zoom cuyo cuadro se está moviendo (se ve sin acercar)
      container.innerHTML = `
        <div class="live">
          <canvas width="360" height="640"></canvas>
          <button class="live-play" title="Reproducir / pausar">▶</button>
          <div class="live-bar"><i></i></div>
          <span class="live-badge">EN VIVO · aproximado</span>
        </div>`;
      this.canvas = container.querySelector("canvas");
      this.ctx = this.canvas.getContext("2d");
      this.btn = container.querySelector(".live-play");
      this.bar = container.querySelector(".live-bar i");
      this.btn.onclick = () => this.toggle();
      this.canvas.onclick = () => this.toggle();
      const redraw = () => this.draw();
      video.addEventListener("seeked", redraw);
      video.addEventListener("loadeddata", redraw);
      video.addEventListener("play", () => { this.btn.textContent = "❚❚"; this.loop(); });
      video.addEventListener("pause", () => { this.btn.textContent = "▶"; this.draw(); });
      video.addEventListener("timeupdate", () => {
        if (this.range && video.currentTime >= this.range[1]) video.currentTime = this.range[0];
      });
    }
    toggle() {
      const v = this.video;
      if (v.paused) {
        if (this.range && (v.currentTime < this.range[0] || v.currentTime >= this.range[1])) v.currentTime = this.range[0];
        v.play();
      } else v.pause();
    }
    setGeometry(g) { this.g = g; this.draw(); }
    setTrack(tr) { this.track = tr; this.draw(); }
    setRange(r) { this.range = r; }
    setZooms(z) { this.zooms = z || []; this.draw(); }
    /** Muestra el cuadro de `z` sobre la vista previa: se arrastra para moverlo y por la esquina para
     *  agrandarlo o achicarlo (mantiene la forma de la pantalla). onChange() tras cada cambio. */
    editZoom(z, onChange) {
      this.stopEdit();
      if (!z) return;
      this.editing = z;
      const live = this.canvas.parentElement;
      const box = document.createElement("div");
      box.className = "zbox";
      box.innerHTML = '<span class="zbox-tag">🔍 zona del zoom</span><i class="zbox-h"></i>';
      live.appendChild(box);
      this.box = box;
      const place = () => {
        z.w = Math.max(0.2, Math.min(0.95, z.w));
        z.x = Math.max(0, Math.min(1 - z.w, z.x));
        z.y = Math.max(0, Math.min(1 - z.w, z.y));
        Object.assign(box.style, { left: z.x * 100 + "%", top: z.y * 100 + "%", width: z.w * 100 + "%", height: z.w * 100 + "%" });
      };
      place();
      let drag = null;
      const down = (e, mode) => {
        e.preventDefault();
        e.stopPropagation();
        const r = live.getBoundingClientRect();
        drag = { mode, px: e.clientX, py: e.clientY, z: { ...z }, rw: r.width, rh: r.height };
        box.setPointerCapture?.(e.pointerId);
      };
      box.addEventListener("pointerdown", (e) => down(e, e.target.classList.contains("zbox-h") ? "size" : "move"));
      box.addEventListener("pointermove", (e) => {
        if (!drag) return;
        const dx = (e.clientX - drag.px) / drag.rw, dy = (e.clientY - drag.py) / drag.rh;
        if (drag.mode === "move") { z.x = drag.z.x + dx; z.y = drag.z.y + dy; }
        else { z.w = drag.z.w + Math.max(dx, dy); }
        place();
        this.draw();
      });
      const up = () => { if (drag) { drag = null; onChange?.(); } };
      box.addEventListener("pointerup", up);
      box.addEventListener("pointercancel", up);
      this.draw();
    }
    stopEdit() {
      this.box?.remove();
      this.box = null;
      this.editing = null;
      this.draw();
    }
    /** Cuánto está acercado el zoom `z` en el segundo `t` (0-1), con la misma rampa que el render. */
    zoomAmount(z, t) {
      const a = z.t, b = z.t + z.d, e = Math.min(0.3, z.d / 2);
      return Math.min(1, Math.max(0, (t - a) / e), Math.max(0, (b - t) / e));
    }
    loop() {
      if (this.video.paused) return;
      this.draw();
      requestAnimationFrame(() => this.loop());
    }
    label(text) {
      const ctx = this.ctx;
      ctx.font = `bold 44px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "rgba(0,0,0,.6)";
      roundRect(ctx, W / 2 - 330, H * 0.82 - 50, 660, 100, 50);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.fillText(text, W / 2, H * 0.82);
    }
    placeholder(text) {
      const ctx = this.ctx;
      ctx.fillStyle = "#111";
      ctx.fillRect(0, 0, W, H);
      this.label(text);
    }
    gpRect() {
      const g = this.g, [x, y, w, h] = g.gameplay;
      if (g.track && this.track?.points?.length) {
        const cx = interp(this.track.points, this.video.currentTime);
        if (cx != null) {
          const sw = g.src[0];
          return [Math.max(0, Math.min(sw - w, cx * sw - w / 2)), y, w, h];
        }
      }
      return [x, y, w, h];
    }
    draw() {
      const g = this.g, v = this.video, ctx = this.ctx;
      const k = this.canvas.width / W;
      ctx.setTransform(k, 0, 0, k, 0, 0);
      if (v.readyState < 2) {  // aún no hay imagen (o está saltando): se deja el último dibujo
        if (!this.drawn) this.placeholder(this.g ? "Cargando el vídeo…" : "Preparando la vista previa…");
        return;
      }
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, W, H);
      if (!g) {  // mientras se busca la webcam: el vídeo tal cual, para que nunca se vea en negro
        const s = Math.min(W / v.videoWidth, H / v.videoHeight) || 1, vw = v.videoWidth * s, vh = v.videoHeight * s;
        ctx.drawImage(v, (W - vw) / 2, (H - vh) / 2, vw, vh);
        this.label("Buscando tu webcam…");
        return;
      }
      this.drawn = true;
      const [gx, gy, gw, gh] = this.gpRect();
      const [dx, dy, dw, dh] = g.gameplay_dst;
      // fondo desenfocado (blur / blur_cam / recuadro que no llena)
      if (g.background || g.fit) {
        const [bx, by, bw, bh] = g.background || [gx, gy, gw, gh];
        ctx.filter = "blur(28px) brightness(0.88)";
        ctx.drawImage(v, bx, by, bw, bh, -40, -40, W + 80, H + 80);
        ctx.filter = "none";
      }
      // juego
      if (g.fit) {
        const s = Math.min(dw / gw, dh / gh), fw = gw * s, fh = gh * s;
        ctx.drawImage(v, gx, gy, gw, gh, dx + (dw - fw) / 2, dy + (dh - fh) / 2, fw, fh);
      } else {
        ctx.drawImage(v, gx, gy, gw, gh, dx, dy, dw, dh);
      }
      // zonas tapadas (webcam / marcador / radar originales): se clona el trozo de al lado
      const sx = dw / gw, sy = dh / gh;
      for (const r of g.hide || []) {
        const [rx, ry, rw, rh] = r;
        if (rx + rw < gx || rx > gx + gw || ry + rh < gy || ry > gy + gh) continue;
        let donor = ry - rh >= 0 ? [rx, ry - rh] : rx - rw >= 0 ? [rx - rw, ry] : [rx + rw, ry];
        ctx.save();
        ctx.beginPath();
        ctx.rect(dx, dy, dw, dh);
        ctx.clip();
        ctx.filter = "blur(3px)";
        ctx.drawImage(v, donor[0], donor[1], rw, rh, dx + (rx - gx) * sx, dy + (ry - gy) * sy, rw * sx, rh * sy);
        ctx.restore();
      }
      ctx.filter = "none";
      // HUD recolocado
      for (const [s, d] of g.hud || []) {
        ctx.save();
        roundRect(ctx, d[0], d[1], d[2], d[3], 10);
        ctx.clip();
        ctx.drawImage(v, s[0], s[1], s[2], s[3], d[0], d[1], d[2], d[3]);
        ctx.restore();
      }
      // facecam
      if (g.facecam_src && g.facecam_dst) {
        const [fx, fy, fw, fh] = g.facecam_src, [cx, cy, cw, ch] = g.facecam_dst;
        if (g.type === "split") {
          ctx.drawImage(v, fx, fy, fw, fh, cx, cy, cw, ch);
        } else {
          ctx.save();
          ctx.shadowColor = "rgba(0,0,0,.55)";
          ctx.shadowBlur = 40;
          ctx.shadowOffsetY = 10;
          roundRect(ctx, cx, cy, cw, ch, g.radius);
          ctx.fillStyle = "#000";
          ctx.fill();
          ctx.restore();
          ctx.save();
          roundRect(ctx, cx, cy, cw, ch, g.radius);
          ctx.clip();
          ctx.drawImage(v, fx, fy, fw, fh, cx, cy, cw, ch);
          ctx.restore();
        }
      }
      // zooms a mano: se reamplía la zona elegida del propio lienzo (sin el gancho ni los subtítulos)
      for (const z of this.zooms) {
        if (z === this.editing) continue;
        const r = this.zoomAmount(z, v.currentTime);
        if (r <= 0) continue;
        const zf = 1 + (1 / z.w - 1) * r, cw = this.canvas.width, ch = this.canvas.height;
        ctx.drawImage(this.canvas, z.x * r * cw, z.y * r * ch, cw / zf, ch / zf, 0, 0, W, H);
        break;
      }
      const t = v.currentTime - (this.range ? this.range[0] : 0);
      // gancho
      let hk = g.hook;
      const hookOn = hk && hk.text && (!hk.duration || t < hk.duration);
      if (hookOn) {
        let size = hk.size;
        ctx.font = `${size}px "${FONT_HOOK}"`;
        const longest = Math.max(...hk.text.toUpperCase().split(/\s+/).map((w) => ctx.measureText(w).width));
        if (longest > 900) { size = Math.floor(size * 900 / longest); ctx.font = `${size}px "${FONT_HOOK}"`; }
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const lines = wrap(ctx, hk.text.toUpperCase(), 940);
        const lh = size * 1.05;
        hk = { ...hk, size };
        let y = hk.y - ((lines.length - 1) * lh) / 2;
        // se desvanece al final, como en el render (hook.fade_out)
        const fade = Math.min(hk.fade ?? 0.15, (hk.duration || 0) * 0.8);
        if (hk.duration && fade > 0) ctx.globalAlpha = Math.max(0, Math.min(1, (hk.duration - t) / fade));
        for (const line of lines) {
          ctx.lineJoin = "round";
          ctx.lineWidth = hk.size * 0.34;
          ctx.strokeStyle = "#fff";
          ctx.strokeText(line, W / 2, y);
          ctx.lineWidth = hk.size * 0.2;
          ctx.strokeStyle = hk.outline;
          ctx.strokeText(line, W / 2, y);
          ctx.fillStyle = hk.fill;
          ctx.fillText(line, W / 2, y);
          y += lh;
        }
        ctx.globalAlpha = 1;
      }
      // subtítulos de ejemplo (cambian cada ~1,2 s)
      const su = g.subs;
      if (su?.on && !hookOn) {
        const words = SAMPLE[Math.floor(Math.max(0, t) / 1.2) % SAMPLE.length];
        const txt = (w) => su.case === "mayusculas" ? w.toUpperCase() : su.case === "minusculas" ? w.toLowerCase() : w;
        ctx.font = `${su.size}px "${FONT_SUBS}"`;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        const parts = words.map(txt);
        const space = ctx.measureText(" ").width;
        const total = parts.reduce((a, w) => a + ctx.measureText(w).width, 0) + space * (parts.length - 1);
        let x = W / 2 - total / 2;
        parts.forEach((w, i) => {
          ctx.lineJoin = "round";
          ctx.lineWidth = Math.max(6, su.size / 8);
          ctx.strokeStyle = "#000";
          ctx.strokeText(w, x, su.y);
          ctx.fillStyle = i === 1 ? su.hl : su.color;
          ctx.fillText(w, x, su.y);
          x += ctx.measureText(w).width + space;
        });
      }
      if (g.handle) {
        ctx.font = `42px "${FONT_SUBS}"`;
        ctx.textAlign = "left";
        ctx.globalAlpha = 0.55;
        ctx.fillStyle = "#fff";
        ctx.fillText(g.handle, 60, H * 0.55);
        ctx.globalAlpha = 1;
      }
      const dur = this.range ? this.range[1] - this.range[0] : v.duration || 1;
      this.bar.style.width = Math.max(0, Math.min(100, (t / dur) * 100)) + "%";
    }
  }

  window.LivePreview = LivePreview;
})();

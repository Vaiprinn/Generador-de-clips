import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { ClipProps, HookProps, SubPhrase, SubtitleProps, WatermarkProps } from "./types";

const fontStack = (font: string) => `"${font}", "${font.split(" ")[0]}", "Arial Black", sans-serif`;

type Layer = { stroke?: number; strokeColor?: string; color: string; shadow?: string };

/**
 * Texto con contorno: se apilan capas del mismo texto (contorno detrás, relleno delante)
 * para que el contorno no se coma el relleno, como en el motor .ass.
 */
const Stacked: React.FC<{ layers: Layer[]; style: React.CSSProperties; children: React.ReactNode }> = ({
  layers, style, children,
}) => (
  <div style={{ display: "grid", ...style }}>
    {layers.map((l, i) => (
      <div
        key={i}
        aria-hidden={i < layers.length - 1}
        style={{
          gridArea: "1 / 1",
          color: l.color,
          WebkitTextStroke: l.stroke ? `${l.stroke}px ${l.strokeColor}` : undefined,
          textShadow: l.shadow,
        }}
      >
        {children}
      </div>
    ))}
  </div>
);

// ------------------------------------------------------------------ subtítulos

const activePhrase = (phrases: SubPhrase[], t: number) => phrases.find((p) => t >= p.start && t < p.end) ?? null;

const entrance = (anim: SubtitleProps["animation"], frame: number, fps: number) => {
  if (anim === "none") return { opacity: 1, transform: "none" };
  const s = spring({ frame, fps, config: { damping: 14, stiffness: 220, mass: 0.6 } });
  if (anim === "fade") return { opacity: interpolate(frame, [0, 4], [0, 1], { extrapolateRight: "clamp" }), transform: "none" };
  if (anim === "slide") return { opacity: s, transform: `translateY(${(1 - s) * 30}px)` };
  return { opacity: Math.min(1, s * 1.5), transform: `scale(${0.82 + 0.18 * s})` }; // pop
};

export const Subtitles: React.FC<{ sub: SubtitleProps }> = ({ sub }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const phrase = activePhrase(sub.phrases, t);
  if (!phrase) return null;

  const local = frame - Math.round(phrase.start * fps);
  const anim = sub.style === "karaoke" ? { opacity: 1, transform: "none" } : entrance(sub.animation, local, fps);
  const s = sub.shadow;
  const shadow =
    sub.style === "resaltado"
      ? `${s}px ${s}px ${Math.round(s * 0.6)}px ${sub.shadowColor}`
      : s > 0
        ? `${s}px ${s}px 0 ${sub.shadowColor}`
        : undefined;

  const words = phrase.words.map((w, i) => {
    const next = phrase.words[i + 1];
    const isActive = sub.style === "karaoke" && t >= w.start && (next ? t < next.start : true);
    let color = sub.primary;
    if (isActive) color = sub.activeColor;
    else if ((sub.highlight || sub.style === "resaltado") && w.hl) color = sub.highlightColor;
    let scale = 1;
    if (isActive) {
      const f = frame - Math.round(w.start * fps);
      scale = interpolate(f, [0, 3, 8], [1, sub.popScale / 100, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    }
    return { text: w.text, color, scale, key: i };
  });

  // Cada palabra es su propio bloque con capas, para poder colorearla y escalarla por separado.
  const line = (layer: "back" | "front") =>
    words.map((w) => (
      <span
        key={w.key}
        style={{
          display: "inline-block",
          margin: `0 ${Math.round(sub.fontSize * 0.14)}px`,
          transform: `scale(${w.scale})`,
          color: layer === "front" ? w.color : sub.outlineColor,
          WebkitTextStroke: layer === "back" && sub.outline > 0 ? `${sub.outline * 2}px ${sub.outlineColor}` : undefined,
          textShadow: layer === "back" ? shadow : undefined,
        }}
      >
        {w.text}
      </span>
    ));

  return (
    <div
      style={{
        position: "absolute",
        left: sub.x - sub.maxWidth / 2,
        top: sub.y,
        width: sub.maxWidth,
        transform: `translateY(-50%) ${anim.transform}`,
        opacity: anim.opacity,
        textAlign: "center",
        fontFamily: fontStack(sub.font),
        fontSize: sub.fontSize,
        fontWeight: 900,
        lineHeight: 1.15,
      }}
    >
      <div style={{ display: "grid" }}>
        <div style={{ gridArea: "1 / 1" }} aria-hidden>{line("back")}</div>
        <div style={{ gridArea: "1 / 1" }}>{line("front")}</div>
      </div>
    </div>
  );
};

// ------------------------------------------------------------------ título gancho

export const Hook: React.FC<{ hook: HookProps }> = ({ hook }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const end = hook.duration > 0 ? Math.round(hook.duration * fps) : durationInFrames;
  if (frame >= end) return null;
  const pop = spring({ frame, fps, config: { damping: 9, stiffness: 180, mass: 0.7 } });
  const out = interpolate(frame, [end - 5, end], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const red = hook.outline;
  const outer = hook.outline + hook.outer;
  return (
    <div
      style={{
        position: "absolute",
        left: 60,
        right: 60,
        top: hook.y,
        transform: `translateY(-50%) rotate(${hook.tilt}deg) scale(${0.6 + 0.4 * pop})`,
        opacity: out,
        textAlign: "center",
        fontFamily: fontStack(hook.font),
        fontSize: hook.fontSize,
        fontWeight: 900,
        fontStyle: "italic",
        lineHeight: 1.05,
        whiteSpace: "pre-line",
      }}
    >
      <Stacked
        style={{}}
        layers={[
          { color: hook.outerColor, stroke: outer * 2, strokeColor: hook.outerColor, shadow: "0 8px 14px rgba(0,0,0,0.45)" },
          { color: hook.outlineColor, stroke: red * 2, strokeColor: hook.outlineColor },
          { color: hook.fill },
        ]}
      >
        {hook.text}
      </Stacked>
    </div>
  );
};

// ------------------------------------------------------------------ marca de agua y barra

export const Watermark: React.FC<{ wm: WatermarkProps }> = ({ wm }) => {
  const tx = wm.align === "left" ? "0" : wm.align === "right" ? "-100%" : "-50%";
  const ty = wm.valign === "top" ? "0" : wm.valign === "bottom" ? "-100%" : "-50%";
  return (
    <div
      style={{
        position: "absolute",
        left: wm.x,
        top: wm.y,
        transform: `translate(${tx}, ${ty})`,
        opacity: wm.opacity,
        fontFamily: fontStack(wm.font),
        fontSize: wm.fontSize,
        fontWeight: 900,
        whiteSpace: "nowrap",
      }}
    >
      <Stacked style={{}} layers={[{ color: "black", stroke: 4, strokeColor: "rgba(0,0,0,0.7)" }, { color: wm.color }]}>
        {wm.text}
      </Stacked>
    </div>
  );
};

export const ProgressBar: React.FC<{ bar: NonNullable<ClipProps["progressBar"]> }> = ({ bar }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const p = durationInFrames > 1 ? frame / (durationInFrames - 1) : 1;
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        height: bar.height,
        top: bar.position === "arriba" ? 0 : undefined,
        bottom: bar.position === "abajo" ? 0 : undefined,
        backgroundColor: "rgba(0,0,0,0.35)",
      }}
    >
      <div style={{ width: `${p * 100}%`, height: "100%", backgroundColor: bar.color }} />
    </div>
  );
};

// Props que genera Python (pipeline/remotion_render.py). Tiempos en segundos,
// posiciones en píxeles del vídeo de salida (1080x1920).

export type Rect = { x: number; y: number; w: number; h: number };

export type SubWord = { text: string; start: number; end: number; hl: boolean };

export type SubPhrase = { start: number; end: number; words: SubWord[] };

export type SubtitleProps = {
  style: "frase" | "resaltado" | "karaoke";
  phrases: SubPhrase[];
  font: string;
  fontSize: number;
  primary: string;
  outlineColor: string;
  outline: number;
  shadowColor: string;
  shadow: number;
  highlight: boolean;
  highlightColor: string;
  activeColor: string;
  popScale: number;
  x: number;
  y: number;
  maxWidth: number;
  animation: "pop" | "slide" | "fade" | "none";
};

export type HookProps = {
  text: string;
  font: string;
  fontSize: number;
  fill: string;
  outlineColor: string;
  outline: number;
  outerColor: string;
  outer: number;
  y: number;
  duration: number; // segundos; 0 = todo el clip
  tilt: number;
};

export type WatermarkProps = {
  text: string;
  font: string;
  fontSize: number;
  color: string;
  opacity: number; // 0-1
  x: number;
  y: number;
  align: "left" | "right" | "center";
  valign: "top" | "middle" | "bottom";
};

export type LayoutProps = {
  type: "facecam_esquina" | "split" | "blur" | "sin_facecam";
  gameplay: Rect;
  gameplayDst: Rect;
  facecamSrc: Rect | null;
  facecamDst: Rect | null;
  background: Rect | null;
  radius: number;
  shadow: boolean;
  blur: number;
};

export type FontFile = { family: string; file: string; weight: string; style: string };

export type ClipProps = {
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  video: { src: string; width: number; height: number };
  layout: LayoutProps;
  subtitles: SubtitleProps | null;
  hook: HookProps | null;
  watermark: WatermarkProps | null;
  progressBar: { color: string; height: number; position: "arriba" | "abajo" } | null;
  zoom: { times: number[]; amount: number } | null;
  fonts: FontFile[];
};

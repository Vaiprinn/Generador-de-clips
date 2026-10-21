import React from "react";
import { Composition } from "remotion";
import { ClipShort } from "./ClipShort";
import type { ClipProps } from "./types";

// Valores de ejemplo solo para Remotion Studio; ClipForge pasa las props reales con --props.
const defaultProps: ClipProps = {
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 150,
  video: { src: "base.mp4", width: 1920, height: 1080 },
  layout: {
    type: "sin_facecam",
    gameplay: { x: 656, y: 0, w: 608, h: 1080 },
    gameplayDst: { x: 0, y: 0, w: 1080, h: 1920 },
    facecamSrc: null,
    facecamDst: null,
    background: null,
    radius: 28,
    shadow: true,
    blur: 30,
  },
  subtitles: null,
  hook: null,
  watermark: null,
  progressBar: null,
  zoom: null,
  fonts: [],
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="ClipShort"
    component={ClipShort}
    durationInFrames={defaultProps.durationInFrames}
    fps={defaultProps.fps}
    width={defaultProps.width}
    height={defaultProps.height}
    defaultProps={defaultProps}
    calculateMetadata={({ props }) => ({
      durationInFrames: Math.max(1, props.durationInFrames),
      fps: props.fps,
      width: props.width,
      height: props.height,
    })}
  />
);

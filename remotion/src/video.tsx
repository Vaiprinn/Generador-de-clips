import React from "react";
import { AbsoluteFill, Easing, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { ClipProps, Rect } from "./types";

// Mismo "punch-in" que el motor FFmpeg, pero con curvas suavizadas.
const RISE = 0.12;
const HOLD = 0.55;
const FALL = 0.4;

export const useZoom = (zoom: ClipProps["zoom"]): number => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!zoom || zoom.times.length === 0) return 1;
  const t = frame / fps;
  let env = 0;
  for (const t0 of zoom.times) {
    if (t < t0 || t > t0 + RISE + HOLD + FALL) continue;
    const up = interpolate(t, [t0, t0 + RISE], [0, 1], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
    });
    const down = interpolate(t, [t0 + RISE + HOLD, t0 + RISE + HOLD + FALL], [1, 0], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic),
    });
    env = Math.max(env, Math.min(up, down));
  }
  return 1 + (zoom.amount - 1) * env;
};

type CropProps = {
  video: ClipProps["video"];
  crop: Rect;
  dst: Rect;
  muted?: boolean;
  scale?: number;
  style?: React.CSSProperties;
  videoStyle?: React.CSSProperties;
};

/** Muestra el trozo `crop` del vídeo original ocupando el rectángulo `dst` de la salida. */
export const Crop: React.FC<CropProps> = ({ video, crop, dst, muted = true, scale = 1, style, videoStyle }) => {
  const k = dst.w / crop.w;
  return (
    <div style={{ position: "absolute", left: dst.x, top: dst.y, width: dst.w, height: dst.h, overflow: "hidden", ...style }}>
      <div style={{ position: "absolute", inset: 0, transform: `scale(${scale})`, transformOrigin: "50% 50%" }}>
        <OffthreadVideo
          src={staticFile(video.src)}
          muted={muted}
          style={{
            position: "absolute",
            left: -crop.x * k,
            top: -crop.y * k,
            width: video.width * k,
            height: video.height * k,
            maxWidth: "none",
            ...videoStyle,
          }}
        />
      </div>
    </div>
  );
};

export const VideoLayers: React.FC<{ props: ClipProps }> = ({ props }) => {
  const { layout, video } = props;
  const zoom = useZoom(props.zoom);

  if (layout.type === "blur" && layout.background) {
    return (
      <AbsoluteFill style={{ backgroundColor: "black" }}>
        <Crop
          video={video}
          crop={layout.background}
          dst={{ x: 0, y: 0, w: props.width, h: props.height }}
          videoStyle={{ filter: `blur(${layout.blur}px) brightness(0.9)` }}
          scale={1.08} // evita bordes transparentes del desenfoque
        />
        <Crop video={video} crop={layout.gameplay} dst={layout.gameplayDst} muted={false} scale={zoom} />
      </AbsoluteFill>
    );
  }

  const facecam =
    layout.facecamSrc && layout.facecamDst ? (
      <Crop
        video={video}
        crop={layout.facecamSrc}
        dst={layout.facecamDst}
        style={
          layout.type === "split"
            ? undefined
            : {
                borderRadius: layout.radius,
                boxShadow: layout.shadow ? "0 10px 34px rgba(0,0,0,0.6)" : undefined,
              }
        }
      />
    ) : null;

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <Crop video={video} crop={layout.gameplay} dst={layout.gameplayDst} muted={false} scale={zoom} />
      {facecam}
    </AbsoluteFill>
  );
};

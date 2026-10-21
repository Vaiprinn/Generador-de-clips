import React, { useEffect, useState } from "react";
import { AbsoluteFill, cancelRender, continueRender, delayRender, staticFile } from "remotion";
import { Hook, ProgressBar, Subtitles, Watermark } from "./text";
import type { ClipProps, FontFile } from "./types";
import { VideoLayers } from "./video";

/** Carga las fuentes de assets/fonts antes de dibujar el primer fotograma. */
const useFonts = (fonts: FontFile[]) => {
  const [handle] = useState(() => delayRender("Cargando fuentes"));
  useEffect(() => {
    Promise.all(
      fonts.map((f) =>
        new FontFace(f.family, `url("${staticFile(f.file)}")`, { weight: f.weight, style: f.style })
          .load()
          .then((face) => (document.fonts as unknown as { add: (f: FontFace) => void }).add(face))
          .catch(() => undefined), // una fuente rota no debe parar el render: se usa la de respaldo
      ),
    )
      .then(() => continueRender(handle))
      .catch((err) => cancelRender(err));
  }, [fonts, handle]);
};

export const ClipShort: React.FC<ClipProps> = (props) => {
  useFonts(props.fonts);
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <VideoLayers props={props} />
      {props.progressBar ? <ProgressBar bar={props.progressBar} /> : null}
      {props.watermark ? <Watermark wm={props.watermark} /> : null}
      {props.subtitles ? <Subtitles sub={props.subtitles} /> : null}
      {props.hook ? <Hook hook={props.hook} /> : null}
    </AbsoluteFill>
  );
};

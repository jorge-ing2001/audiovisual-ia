import React from "react";
import {
  AbsoluteFill,
  Img,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

import type { SubtitleCue, TimelineProps, TimelineScene } from "./types";

// Las imágenes de escena llegan como rutas relativas al publicDir (AIV_MEDIA_ROOT);
// `staticFile` las resuelve contra el servidor del bundle. Las URLs absolutas
// (http(s)/data) se usan tal cual.
const resolveSrc = (src: string): string =>
  src.includes("://") || src.startsWith("data:") ? src : staticFile(src);

// Composición de render (FEAT-005): por cada escena del timeline dibuja su imagen a
// pantalla completa durante `durationInFrames` y superpone la pista de subtítulos.
// Las escenas se encadenan con <Sequence> respetando sus duraciones. El audio NO se
// mezcla aquí: la mezcla final se muxea con FFmpeg sobre el vídeo resultante.

const SceneLayer: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeS = frame / fps;

  const activeCue: SubtitleCue | undefined = (scene.cues ?? []).find(
    (cue) => timeS >= cue.startS && timeS < cue.endS,
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {scene.imageSrc ? (
        <Img
          src={resolveSrc(scene.imageSrc)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : (
        <AbsoluteFill
          style={{
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "#0a0a0a",
            color: "#e5e5e5",
            fontFamily: "sans-serif",
            fontSize: 32,
          }}
        >
          {scene.narrationText ?? scene.id}
        </AbsoluteFill>
      )}

      {activeCue ? (
        <AbsoluteFill
          style={{
            alignItems: "center",
            justifyContent: "flex-end",
            padding: "0 6% 6%",
          }}
        >
          <span
            style={{
              fontFamily: "sans-serif",
              fontSize: 48,
              lineHeight: 1.25,
              fontWeight: 700,
              color: "#ffffff",
              textAlign: "center",
              textShadow: "0 2px 6px rgba(0,0,0,0.9)",
              backgroundColor: "rgba(0,0,0,0.45)",
              padding: "0.2em 0.5em",
              borderRadius: 8,
              maxWidth: "88%",
            }}
          >
            {activeCue.text}
          </span>
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};

export const TimelineComposition: React.FC<TimelineProps> = ({ scenes }) => {
  let from = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {scenes.map((scene) => {
        const durationInFrames = Math.max(1, Math.round(scene.durationInFrames));
        const sequence = (
          <Sequence
            key={scene.id}
            from={from}
            durationInFrames={durationInFrames}
            name={scene.id}
          >
            <SceneLayer scene={scene} />
          </Sequence>
        );
        from += durationInFrames;
        return sequence;
      })}
    </AbsoluteFill>
  );
};

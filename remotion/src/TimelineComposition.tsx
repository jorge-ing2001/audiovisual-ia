import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

import type { SubtitleCue, TimelineProps, TimelineScene } from "./types";

// Las imágenes/clips de escena llegan como rutas relativas al publicDir (AIV_MEDIA_ROOT);
// `staticFile` las resuelve contra el servidor del bundle. Las URLs absolutas
// (http(s)/data) se usan tal cual.
const resolveSrc = (src: string): string =>
  src.includes("://") || src.startsWith("data:") ? src : staticFile(src);

// Palabra clave del cue resaltada en amarillo (estilo TikTok 3d). Selección
// DETERMINISTA en el MVP: la palabra más larga del cue (desempate por la primera).
const keywordIndex = (words: string[]): number => {
  let best = 0;
  let bestLen = -1;
  for (let i = 0; i < words.length; i += 1) {
    const len = words[i].replace(/[^\p{L}\p{N}]/gu, "").length;
    if (len > bestLen) {
      bestLen = len;
      best = i;
    }
  }
  return best;
};

// Overlay de subtítulos estilo TikTok (producción 3d): texto grande en negrita, blanco
// con contorno negro grueso (múltiples sombras), centrado en la zona segura vertical,
// con UNA palabra clave en amarillo. Ver docs/ENGINE-3D-MVP-STYLIZED.md §5.
const blackStroke =
  "-3px -3px 0 #000, 3px -3px 0 #000, -3px 3px 0 #000, 3px 3px 0 #000, " +
  "0 3px 0 #000, 0 -3px 0 #000, 3px 0 0 #000, -3px 0 0 #000, 0 0 10px rgba(0,0,0,0.9)";

const TikTokSubtitle: React.FC<{ cue: SubtitleCue }> = ({ cue }) => {
  const words = cue.text.split(/\s+/).filter((w) => w.length > 0);
  const highlight = keywordIndex(words);
  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: "center",
        // Zona segura vertical: no pegado a los bordes (centrado-bajo).
        padding: "0 8% 22%",
        flexDirection: "column-reverse",
      }}
    >
      <span
        style={{
          fontFamily: "sans-serif",
          fontSize: 72,
          lineHeight: 1.2,
          fontWeight: 900,
          color: "#ffffff",
          textAlign: "center",
          textTransform: "uppercase",
          textShadow: blackStroke,
          maxWidth: "84%",
        }}
      >
        {words.map((word, i) => (
          <React.Fragment key={i}>
            {i > 0 ? " " : ""}
            <span style={i === highlight ? { color: "#ffe100" } : undefined}>
              {word}
            </span>
          </React.Fragment>
        ))}
      </span>
    </AbsoluteFill>
  );
};

// Overlay de subtítulos clásico (producción 2d): caja semitransparente abajo.
const ClassicSubtitle: React.FC<{ cue: SubtitleCue }> = ({ cue }) => (
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
      {cue.text}
    </span>
  </AbsoluteFill>
);

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

  // Producción 3d: el clip de vídeo manda (y activa el estilo de subtítulos TikTok).
  const isVideo = Boolean(scene.videoSrc);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {isVideo ? (
        <OffthreadVideo
          src={resolveSrc(scene.videoSrc as string)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : scene.imageSrc ? (
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
        isVideo ? (
          <TikTokSubtitle cue={activeCue} />
        ) : (
          <ClassicSubtitle cue={activeCue} />
        )
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

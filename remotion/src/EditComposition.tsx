import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

import type {
  EditAudioClipProps,
  EditProps,
  EditSubtitleClipProps,
  EditSubtitleStyleProps,
  EditTransformProps,
  EditTransitionProps,
  EditVisualClipProps,
} from "./types";

// Composición MULTIPISTA del editor (FEAT-003). Es la contraparte de
// `TimelineComposition` (que NO se toca) y se comparte entre:
//   - el preview en tiempo real (@remotion/player en el navegador, con audio), y
//   - el render headless de export (muted=true; el AudioMixer compone el audio final).
// Recibe como inputProps el resultado de compile_remotion_props(edit_project):
// pistas visuales (clips con from/durationInFrames, src, transform, transición) +
// pista de subtítulos (clips con text + style) + pistas de audio (voz/música/SFX).

// Las src de recurso llegan como ruta relativa al publicDir (render headless) o como
// URL http(s)/data (Player). `staticFile` resuelve las relativas contra el bundle.
const resolveSrc = (src: string): string =>
  src.includes("://") || src.startsWith("data:") ? src : staticFile(src);

// --------------------------------------------------------------------------- //
// Subtítulos parametrizados por estilo (generaliza Classic/TikTok de Timeline)
// --------------------------------------------------------------------------- //

const blackStroke =
  "-3px -3px 0 #000, 3px -3px 0 #000, -3px 3px 0 #000, 3px 3px 0 #000, " +
  "0 3px 0 #000, 0 -3px 0 #000, 3px 0 0 #000, -3px 0 0 #000, 0 0 10px rgba(0,0,0,0.9)";

const justifyForPosition = (
  position: EditSubtitleStyleProps["position"],
): React.CSSProperties["justifyContent"] => {
  if (position === "top") return "flex-start";
  if (position === "center") return "center";
  return "flex-end";
};

const SubtitleOverlay: React.FC<{ clip: EditSubtitleClipProps }> = ({ clip }) => {
  const style: EditSubtitleStyleProps = {
    fontFamily: "Inter, sans-serif",
    fontSize: 48,
    color: "#FFFFFF",
    bgColor: null,
    bold: true,
    outline: true,
    position: "bottom",
    x: 0,
    y: 0,
    preset: "classic",
    ...(clip.style ?? {}),
  };

  const isTikTok = style.preset === "tiktok";
  const padByPosition =
    style.position === "top"
      ? "8% 6% 0"
      : style.position === "center"
        ? "0 6%"
        : isTikTok
          ? "0 8% 22%"
          : "0 6% 6%";

  const spanStyle: React.CSSProperties = {
    fontFamily: style.fontFamily,
    fontSize: style.fontSize,
    lineHeight: 1.25,
    fontWeight: style.bold ? 900 : 500,
    color: style.color,
    textAlign: "center",
    maxWidth: "88%",
    transform: `translate(${style.x}px, ${style.y}px)`,
    textTransform: isTikTok ? "uppercase" : "none",
    textShadow: style.outline
      ? isTikTok
        ? blackStroke
        : "0 2px 6px rgba(0,0,0,0.9)"
      : "none",
    ...(style.bgColor
      ? {
          backgroundColor: style.bgColor,
          padding: "0.2em 0.5em",
          borderRadius: 8,
        }
      : isTikTok
        ? {}
        : {
            backgroundColor: "rgba(0,0,0,0.45)",
            padding: "0.2em 0.5em",
            borderRadius: 8,
          }),
  };

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: justifyForPosition(style.position),
        padding: padByPosition,
      }}
    >
      <span style={spanStyle}>{clip.text}</span>
    </AbsoluteFill>
  );
};

// --------------------------------------------------------------------------- //
// Clip visual: <Img>/<OffthreadVideo> con transform + transiciones fade
// --------------------------------------------------------------------------- //

const transformToCss = (
  transform: EditTransformProps | null | undefined,
): { objectFit: "cover" | "contain"; transform: string } => {
  if (!transform) return { objectFit: "cover", transform: "none" };
  const parts = [
    `translate(${transform.x}px, ${transform.y}px)`,
    `scale(${transform.scale})`,
  ];
  if (transform.rotation) parts.push(`rotate(${transform.rotation}deg)`);
  return {
    objectFit: transform.fit === "contain" ? "contain" : "cover",
    transform: parts.join(" "),
  };
};

const VisualClip: React.FC<{ clip: EditVisualClipProps }> = ({ clip }) => {
  const frame = useCurrentFrame();
  const { objectFit, transform } = transformToCss(clip.transform);

  // Transiciones fade básicas: la opacidad se interpola sobre los primeros/últimos
  // frames del clip (durationInFrames del propio clip, ya que estamos dentro de su
  // <Sequence>, por lo que `frame` es local al clip).
  const inT: EditTransitionProps | null | undefined = clip.transitionIn;
  const outT: EditTransitionProps | null | undefined = clip.transitionOut;
  const total = Math.max(1, clip.durationInFrames);

  let opacity = 1;
  if (inT && inT.type === "fade" && inT.durationInFrames > 0) {
    opacity *= interpolate(frame, [0, inT.durationInFrames], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  }
  if (outT && outT.type === "fade" && outT.durationInFrames > 0) {
    const start = total - outT.durationInFrames;
    opacity *= interpolate(frame, [start, total], [1, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  }

  const mediaStyle: React.CSSProperties = {
    width: "100%",
    height: "100%",
    objectFit,
    transform,
  };

  return (
    <AbsoluteFill style={{ opacity }}>
      {clip.src ? (
        clip.kind === "video" ? (
          <OffthreadVideo src={resolveSrc(clip.src)} style={mediaStyle} muted />
        ) : (
          <Img src={resolveSrc(clip.src)} style={mediaStyle} />
        )
      ) : (
        <AbsoluteFill
          style={{
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "#0a0a0a",
            color: "#737373",
            fontFamily: "sans-serif",
            fontSize: 28,
          }}
        >
          {clip.kind} · recurso sin resolver
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};

// --------------------------------------------------------------------------- //
// Clip de audio (solo preview; el export va muted=true)
// --------------------------------------------------------------------------- //

const AudioClip: React.FC<{ clip: EditAudioClipProps }> = ({ clip }) => {
  if (!clip.src) return null;
  return (
    <Audio
      src={resolveSrc(clip.src)}
      volume={Math.max(0, clip.volume)}
      // El clip ya está posicionado por su <Sequence>; reproducimos desde el inicio.
    />
  );
};

// --------------------------------------------------------------------------- //
// Composición principal
// --------------------------------------------------------------------------- //

export const EditComposition: React.FC<EditProps> = ({
  visualClips,
  subtitleClips,
  audioClips,
  muted,
}) => {
  const { fps } = useVideoConfig();
  const clamp1 = (n: number) => Math.max(1, Math.round(n));

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {/* Pistas visuales: cada clip en su ventana temporal (orden por z/pista). */}
      {(visualClips ?? []).map((clip) => (
        <Sequence
          key={clip.id}
          from={Math.max(0, Math.round(clip.from))}
          durationInFrames={clamp1(clip.durationInFrames)}
          name={`visual:${clip.id}`}
          layout="none"
        >
          <VisualClip clip={clip} />
        </Sequence>
      ))}

      {/* Pista de subtítulos: overlays parametrizados por estilo. */}
      {(subtitleClips ?? []).map((clip) => (
        <Sequence
          key={clip.id}
          from={Math.max(0, Math.round(clip.from))}
          durationInFrames={clamp1(clip.durationInFrames)}
          name={`sub:${clip.id}`}
          layout="none"
        >
          <SubtitleOverlay clip={clip} />
        </Sequence>
      ))}

      {/* Audio por clip (voz/música/SFX) — SOLO preview; el export va muted. */}
      {!muted
        ? (audioClips ?? []).map((clip) => (
            <Sequence
              key={clip.id}
              from={Math.max(0, Math.round(clip.from))}
              durationInFrames={clamp1(clip.durationInFrames)}
              name={`audio:${clip.id}`}
              layout="none"
            >
              <AudioClip clip={clip} />
            </Sequence>
          ))
        : null}
    </AbsoluteFill>
  );
};

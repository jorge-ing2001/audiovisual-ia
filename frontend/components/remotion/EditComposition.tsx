"use client";

// Composición MULTIPISTA del editor para el PREVIEW (@remotion/player en el navegador).
//
// Es el espejo tipado de remotion/src/EditComposition.tsx (misma estructura: pistas
// visuales con <Sequence>+<Img>/<OffthreadVideo>, subtítulos parametrizados por estilo y
// audio por clip con <Audio>). El componente recibe los MISMOS inputProps que el render
// headless de export (compilados por compileEditInputProps / compile_remotion_props), de
// modo que "lo que ves en el preview = lo que exportas". En el Player las `src` de media
// ya llegan como URL HTTP (assetDownloadUrl), así que no se usa staticFile.

import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

import type {
  EditAudioClipInput,
  EditInputProps,
  EditSubtitleClipInput,
  EditSubtitleStyleInput,
  EditTransformInput,
  EditTransitionInput,
  EditVisualClipInput,
} from "@/lib/editor/compose";

const blackStroke =
  "-3px -3px 0 #000, 3px -3px 0 #000, -3px 3px 0 #000, 3px 3px 0 #000, " +
  "0 3px 0 #000, 0 -3px 0 #000, 3px 0 0 #000, -3px 0 0 #000, 0 0 10px rgba(0,0,0,0.9)";

const DEFAULT_SUBTITLE_STYLE: EditSubtitleStyleInput = {
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
};

const justifyForPosition = (
  position: EditSubtitleStyleInput["position"],
): React.CSSProperties["justifyContent"] => {
  if (position === "top") return "flex-start";
  if (position === "center") return "center";
  return "flex-end";
};

const SubtitleOverlay: React.FC<{ clip: EditSubtitleClipInput }> = ({ clip }) => {
  const style: EditSubtitleStyleInput = {
    ...DEFAULT_SUBTITLE_STYLE,
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
      ? { backgroundColor: style.bgColor, padding: "0.2em 0.5em", borderRadius: 8 }
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

const transformToCss = (
  transform: EditTransformInput | null,
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

const VisualClip: React.FC<{ clip: EditVisualClipInput }> = ({ clip }) => {
  const frame = useCurrentFrame();
  const { objectFit, transform } = transformToCss(clip.transform);
  const inT: EditTransitionInput | null = clip.transitionIn;
  const outT: EditTransitionInput | null = clip.transitionOut;
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
          <OffthreadVideo src={clip.src} style={mediaStyle} muted />
        ) : (
          <Img src={clip.src} style={mediaStyle} />
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

const AudioClip: React.FC<{ clip: EditAudioClipInput }> = ({ clip }) => {
  if (!clip.src) return null;
  return <Audio src={clip.src} volume={Math.max(0, clip.volume)} />;
};

export const EditComposition: React.FC<EditInputProps> = ({
  visualClips,
  subtitleClips,
  audioClips,
  muted,
}) => {
  useVideoConfig();
  const clamp1 = (n: number) => Math.max(1, Math.round(n));

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
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

export default EditComposition;

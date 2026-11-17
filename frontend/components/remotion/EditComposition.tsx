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
  EditOverlayClipInput,
  EditSubtitleClipInput,
  EditSubtitleStyleInput,
  EditTitleClipInput,
  EditTitleStyleInput,
  EditTransformInput,
  EditTransitionInput,
  EditVisualClipInput,
} from "@/lib/editor/compose";
import { loadUsedFonts } from "@/lib/editor/fonts";

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
  karaoke: false,
  highlightColor: "#FFE34D",
  highlightScale: 1,
};

const justifyForPosition = (
  position: EditSubtitleStyleInput["position"],
): React.CSSProperties["justifyContent"] => {
  if (position === "top") return "flex-start";
  if (position === "center") return "center";
  return "flex-end";
};

// Convierte "#RRGGBB" + opacidad (0..1) en una cadena rgba() para el fondo de la caja.
const hexToRgba = (hex: string, opacity: number): string => {
  const m = /^#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/.exec(hex.trim());
  if (!m) return hex;
  const r = parseInt(m[1], 16);
  const g = parseInt(m[2], 16);
  const b = parseInt(m[3], 16);
  const a = Math.max(0, Math.min(1, opacity));
  return `rgba(${r}, ${g}, ${b}, ${a})`;
};

const SubtitleOverlay: React.FC<{
  clip: EditSubtitleClipInput;
  safeTop?: number;
  safeBottom?: number;
}> = ({ clip, safeTop = 0, safeBottom = 0 }) => {
  // `frame` es local al clip porque el overlay va dentro de la <Sequence> del clip
  // (igual que VisualClip con las transiciones fade). Se usa para el karaoke.
  const frame = useCurrentFrame();
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

  // Decoración fina (contorno/sombra/fondo): solo activa cuando llegan los campos nuevos.
  // Con los defaults (ausentes), se conserva EXACTAMENTE el aspecto heredado (regresión).
  const hasStroke = (style.outlineWidth ?? 0) > 0;
  const hasShadow = style.shadow === true;
  const hasCustomBg = style.bgOpacity !== undefined && style.bgColor != null;

  const shadowParts: string[] = [];
  if (hasShadow) {
    shadowParts.push(
      `${style.shadowX ?? 0}px ${style.shadowY ?? 0}px ${style.shadowBlur ?? 0}px ${
        style.shadowColor ?? "#000000"
      }`,
    );
  }
  let textShadow: string;
  if (hasStroke || hasShadow) {
    textShadow = shadowParts.length > 0 ? shadowParts.join(", ") : "none";
  } else {
    textShadow = style.outline
      ? isTikTok
        ? blackStroke
        : "0 2px 6px rgba(0,0,0,0.9)"
      : "none";
  }

  const bgStyle: React.CSSProperties = hasCustomBg
    ? {
        backgroundColor: hexToRgba(style.bgColor as string, style.bgOpacity ?? 1),
        padding: `${style.bgPadding ?? 0.2}em 0.5em`,
        borderRadius: style.bgRadius ?? 8,
      }
    : style.bgColor
      ? { backgroundColor: style.bgColor, padding: "0.2em 0.5em", borderRadius: 8 }
      : isTikTok
        ? {}
        : {
            backgroundColor: "rgba(0,0,0,0.45)",
            padding: "0.2em 0.5em",
            borderRadius: 8,
          };

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
    textShadow,
    ...(hasStroke
      ? {
          WebkitTextStroke: `${style.outlineWidth}px ${style.outlineColor ?? "#000000"}`,
        }
      : {}),
    ...bgStyle,
  };

  const words = clip.words ?? [];
  const karaokeActive = style.karaoke === true && words.length > 0;

  // Respeta las zonas seguras verticales del formato (9:16).
  const safePadding = {
    paddingTop: safeTop > 0 ? `${(safeTop * 100).toFixed(2)}%` : undefined,
    paddingBottom: safeBottom > 0 ? `${(safeBottom * 100).toFixed(2)}%` : undefined,
  };

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: justifyForPosition(style.position),
        padding: padByPosition,
        ...safePadding,
      }}
    >
      {karaokeActive ? (
        <span style={spanStyle}>
          {words.map((word, i) => {
            const active =
              frame >= word.from && frame < word.from + word.durationInFrames;
            const wordStyle: React.CSSProperties = active
              ? {
                  color: style.highlightColor,
                  display: "inline-block",
                  transform: `scale(${style.highlightScale ?? 1})`,
                  transformOrigin: "center",
                }
              : { display: "inline-block" };
            return (
              <React.Fragment key={`${word.text}-${i}`}>
                <span style={wordStyle}>{word.text}</span>
                {i < words.length - 1 ? " " : null}
              </React.Fragment>
            );
          })}
        </span>
      ) : (
        <span style={spanStyle}>{clip.text}</span>
      )}
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
  const { fps } = useVideoConfig();
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

  // FEAT-005: el <OffthreadVideo> no reproduce su audio de origen cuando el clip está
  // detachado (muted). Por defecto el preview/export ya iba mudo (el audio lo componen los
  // clips <Audio>/el AudioMixer), de modo que `?? true` preserva el baseline.
  const videoMuted = clip.muted ?? true;
  // FEAT-006: velocidad de reproducción del vídeo (playbackRate). El reverse real del
  // vídeo NO se reproduce en preview (Remotion no reproduce al revés sin origen
  // pre-invertido): se muestra el clip normal con un indicador "invertido (se aplica en
  // export)". El reverse de VÍDEO se difiere al camino de export (ver findings §8).
  const speed = clip.speed ?? null;
  const playbackRate = speed && speed.rate > 0 ? speed.rate : 1;
  const reverseIndicator = !!(speed && speed.reverse);
  // FEAT-006: congelación de fotograma. Se fija el <OffthreadVideo> al frame del origen
  // (startFrom/endAt al mismo frame) para mostrar el fotograma fijo durante el tramo.
  const freeze = clip.freeze ?? null;

  return (
    <AbsoluteFill style={{ opacity }}>
      {clip.src ? (
        clip.kind === "video" ? (
          freeze ? (
            <OffthreadVideo
              src={clip.src}
              style={mediaStyle}
              muted={videoMuted}
              startFrom={Math.max(0, Math.round(freeze.atFrame))}
              endAt={Math.max(1, Math.round(freeze.atFrame) + 1)}
            />
          ) : (
            <OffthreadVideo
              src={clip.src}
              style={mediaStyle}
              muted={videoMuted}
              playbackRate={playbackRate}
            />
          )
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
      {reverseIndicator ? (
        <div
          style={{
            position: "absolute",
            left: 12,
            bottom: 12,
            padding: "4px 10px",
            borderRadius: 6,
            backgroundColor: "rgba(0,0,0,0.6)",
            color: "#fde047",
            fontFamily: "sans-serif",
            fontSize: Math.max(12, Math.round(fps * 0.6)),
            fontWeight: 700,
          }}
        >
          invertido (se aplica en export)
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

// --------------------------------------------------------------------------- //
// Clip de título (texto animado: intro / lower-third / créditos)
// --------------------------------------------------------------------------- //

const DEFAULT_TITLE_STYLE: EditTitleStyleInput = {
  fontFamily: "Inter, sans-serif",
  fontSize: 96,
  color: "#FFFFFF",
  bold: true,
  align: "center",
  anchorX: 0.5,
  anchorY: 0.5,
  kind: "plain",
  outlineColor: "#000000",
  outlineWidth: 0,
  shadow: false,
  shadowColor: "#000000",
  shadowBlur: 0,
  shadowX: 0,
  shadowY: 0,
  bgOpacity: 1,
  bgPadding: 0.2,
  bgRadius: 8,
};

const TitleOverlay: React.FC<{ clip: EditTitleClipInput }> = ({ clip }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const style: EditTitleStyleInput = {
    ...DEFAULT_TITLE_STYLE,
    ...(clip.style ?? {}),
  };
  const anim = clip.animation ?? null;
  const total = Math.max(1, clip.durationInFrames);
  const animFrames = Math.max(
    1,
    anim ? anim.durationInFrames : Math.round(0.8 * fps),
  );
  const dir = anim?.direction ?? "both";
  const type = anim?.type ?? "fade";

  const inProg =
    dir === "out"
      ? 1
      : interpolate(frame, [0, animFrames], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
  const outProg =
    dir === "in"
      ? 0
      : interpolate(frame, [total - animFrames, total], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });

  let opacity = 1;
  let translateX = 0;
  let translateY = 0;
  let scale = 1;
  let visibleText = clip.text;

  if (type === "fade") {
    opacity = Math.min(inProg, 1 - outProg);
  } else if (type === "slide_up") {
    opacity = Math.min(inProg, 1 - outProg);
    translateY = (1 - inProg) * 40 + outProg * -40;
  } else if (type === "slide_down") {
    opacity = Math.min(inProg, 1 - outProg);
    translateY = (1 - inProg) * -40 + outProg * 40;
  } else if (type === "scale_in") {
    opacity = Math.min(inProg, 1 - outProg);
    scale = 0.7 + 0.3 * inProg;
  } else if (type === "typewriter") {
    const chars = Math.round(
      interpolate(frame, [0, animFrames], [0, clip.text.length], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      }),
    );
    visibleText = clip.text.slice(0, chars);
    opacity = 1 - outProg;
  } else if (type === "none") {
    opacity = 1;
  }

  const isCredits = type === "credits_roll";
  const rollProgress = isCredits
    ? interpolate(frame, [0, total], [1, -1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  const hasStroke = style.outlineWidth > 0;
  const shadow = style.shadow
    ? `${style.shadowX}px ${style.shadowY}px ${style.shadowBlur}px ${style.shadowColor}`
    : "none";
  const hasBg = style.bgOpacity > 0 && style.bgOpacity < 1;

  const spanStyle: React.CSSProperties = {
    fontFamily: style.fontFamily,
    fontSize: style.fontSize,
    lineHeight: 1.2,
    fontWeight: style.bold ? 900 : 500,
    color: style.color,
    textAlign: style.align,
    maxWidth: "80%",
    whiteSpace: "pre-wrap",
    textShadow: shadow,
    ...(hasStroke
      ? { WebkitTextStroke: `${style.outlineWidth}px ${style.outlineColor}` }
      : {}),
    ...(hasBg
      ? {
          backgroundColor: hexToRgba("#000000", style.bgOpacity),
          padding: `${style.bgPadding}em 0.6em`,
          borderRadius: style.bgRadius,
        }
      : {}),
  };

  const leftPct = `${(style.anchorX * 100).toFixed(3)}%`;
  const topPct = isCredits
    ? `${((rollProgress * 0.5 + 0.5) * 100).toFixed(3)}%`
    : `${(style.anchorY * 100).toFixed(3)}%`;

  return (
    <AbsoluteFill style={{ opacity }}>
      <div
        style={{
          position: "absolute",
          left: leftPct,
          top: topPct,
          transform: `translate(-50%, -50%) translate(${translateX}px, ${translateY}px) scale(${scale})`,
          transformOrigin: "center",
          display: "flex",
          justifyContent: "center",
          width: "100%",
        }}
      >
        <span style={spanStyle}>{visibleText}</span>
      </div>
    </AbsoluteFill>
  );
};

// --------------------------------------------------------------------------- //
// Clip de overlay (imagen / logo / marca de agua con mosaico)
// --------------------------------------------------------------------------- //

const OverlayClip: React.FC<{ clip: EditOverlayClipInput }> = ({ clip }) => {
  const { width } = useVideoConfig();
  if (!clip.src) return null;
  const overlay = clip.overlay ?? {
    opacity: 1,
    anchorX: 0.5,
    anchorY: 0.5,
    widthFrac: 0.2,
    rotation: 0,
    tile: false,
  };
  const src = clip.src;
  const imgWidth = Math.max(1, Math.round(overlay.widthFrac * width));

  if (overlay.tile) {
    return (
      <AbsoluteFill
        style={{
          opacity: overlay.opacity,
          backgroundImage: `url(${src})`,
          backgroundRepeat: "repeat",
          backgroundSize: `${imgWidth}px auto`,
          transform: overlay.rotation ? `rotate(${overlay.rotation}deg)` : undefined,
        }}
      />
    );
  }

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          left: `${(overlay.anchorX * 100).toFixed(3)}%`,
          top: `${(overlay.anchorY * 100).toFixed(3)}%`,
          transform: `translate(-50%, -50%) rotate(${overlay.rotation}deg)`,
          opacity: overlay.opacity,
        }}
      >
        <Img src={src} style={{ width: imgWidth, height: "auto", display: "block" }} />
      </div>
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
  titleClips,
  overlayClips,
  muted,
  safeTop,
  safeBottom,
}) => {
  useVideoConfig();
  const clamp1 = (n: number) => Math.max(1, Math.round(n));

  // Carga las tipografías del catálogo usadas por subtítulos/títulos ANTES de pintar, igual
  // que el espejo de export (FEAT-007): el Player espera la fuente vía el delayRender del
  // loader, de modo que el preview muestra la MISMA tipografía que el MP4 exportado.
  loadUsedFonts([
    ...(subtitleClips ?? []).map((c) => c.style?.fontFamily),
    ...(titleClips ?? []).map((c) => c.style?.fontFamily),
  ]);

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

      {(overlayClips ?? []).map((clip) => (
        <Sequence
          key={clip.id}
          from={Math.max(0, Math.round(clip.from))}
          durationInFrames={clamp1(clip.durationInFrames)}
          name={`overlay:${clip.id}`}
          layout="none"
        >
          <OverlayClip clip={clip} />
        </Sequence>
      ))}

      {(titleClips ?? []).map((clip) => (
        <Sequence
          key={clip.id}
          from={Math.max(0, Math.round(clip.from))}
          durationInFrames={clamp1(clip.durationInFrames)}
          name={`title:${clip.id}`}
          layout="none"
        >
          <TitleOverlay clip={clip} />
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
          <SubtitleOverlay clip={clip} safeTop={safeTop} safeBottom={safeBottom} />
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

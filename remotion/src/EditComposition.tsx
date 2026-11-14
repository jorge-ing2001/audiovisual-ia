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
  EditOverlayClipProps,
  EditProps,
  EditSubtitleClipProps,
  EditSubtitleStyleProps,
  EditTitleClipProps,
  EditTitleStyleProps,
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
  clip: EditSubtitleClipProps;
  safeTop?: number;
  safeBottom?: number;
}> = ({ clip, safeTop = 0, safeBottom = 0 }) => {
  // `frame` es local al clip porque el overlay va dentro de la <Sequence> del clip
  // (igual que VisualClip con las transiciones fade). Se usa para el karaoke.
  const frame = useCurrentFrame();
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
    karaoke: false,
    highlightColor: "#FFE34D",
    highlightScale: 1,
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

  // textShadow: suma contorno (si aplica) y sombra (si aplica); si no, el heredado.
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
    // Con contorno fino usamos WebkitTextStroke; la sombra va aparte en textShadow.
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

  // Respeta las zonas seguras verticales del formato (9:16): reserva márgenes
  // superior/inferior en fracción del alto para no quedar bajo la UI de la red social.
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
// Clip de título (texto animado: intro / lower-third / créditos)
// --------------------------------------------------------------------------- //

const DEFAULT_TITLE_STYLE: EditTitleStyleProps = {
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

const TitleOverlay: React.FC<{ clip: EditTitleClipProps }> = ({ clip }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const style: EditTitleStyleProps = {
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

  // Entrada (0..animFrames) y salida (total-animFrames..total) en 0..1.
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

  // credits_roll: desplazamiento vertical continuo a lo largo de TODO el clip (de abajo
  // a arriba), ignorando anchor_y (los créditos recorren el frame).
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

  // Posición por anclaje (fracción del frame). En credits_roll el texto recorre el eje Y.
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

const OverlayClip: React.FC<{ clip: EditOverlayClipProps }> = ({ clip }) => {
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
  const src = resolveSrc(clip.src);
  const imgWidth = Math.max(1, Math.round(overlay.widthFrac * width));

  if (overlay.tile) {
    // Marca de agua en mosaico: repite la imagen por todo el frame con la opacidad dada.
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
  titleClips,
  overlayClips,
  muted,
  safeTop,
  safeBottom,
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

      {/* Overlays (logos/marcas de agua): por encima de las visuales. */}
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

      {/* Títulos animados: por encima de visuales y overlays. */}
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

      {/* Pista de subtítulos: overlays parametrizados por estilo. */}
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

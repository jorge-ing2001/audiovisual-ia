import { AbsoluteFill } from "remotion";

import type { TimelineProps } from "./types";

// Composición de ejemplo del andamiaje (FEAT-001): acepta props de timeline
// pero aún NO renderiza escenas. La lógica de composición se implementa en FEAT-005.
export const TimelineComposition: React.FC<TimelineProps> = ({ scenes }) => {
  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0a0a0a",
        color: "#e5e5e5",
        fontFamily: "sans-serif",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div style={{ fontSize: 48 }}>audiovisual-ia</div>
      <div style={{ fontSize: 24, opacity: 0.6 }}>
        Timeline con {scenes.length} escena(s) — andamiaje FEAT-001
      </div>
    </AbsoluteFill>
  );
};

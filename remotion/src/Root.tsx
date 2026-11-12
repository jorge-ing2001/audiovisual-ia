import React from "react";
import { Composition } from "remotion";

import { TimelineComposition } from "./TimelineComposition";
import {
  defaultTimelineProps,
  totalDurationInFrames,
  type TimelineProps,
} from "./types";

// Registro de composiciones. La duración, el fps y la resolución se derivan del
// timeline real recibido como inputProps mediante `calculateMetadata` (FEAT-005),
// de modo que el MP4 dura exactamente la suma de las escenas.
export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Timeline"
      component={TimelineComposition}
      durationInFrames={totalDurationInFrames(defaultTimelineProps)}
      fps={defaultTimelineProps.fps}
      width={defaultTimelineProps.width}
      height={defaultTimelineProps.height}
      defaultProps={defaultTimelineProps}
      calculateMetadata={({ props }: { props: TimelineProps }) => {
        return {
          durationInFrames: totalDurationInFrames(props),
          fps: props.fps,
          width: props.width,
          height: props.height,
        };
      }}
    />
  );
};

import React from "react";
import { Composition } from "remotion";

import { EditComposition } from "./EditComposition";
import { TimelineComposition } from "./TimelineComposition";
import {
  defaultEditProps,
  defaultTimelineProps,
  editDurationInFrames,
  totalDurationInFrames,
  type EditProps,
  type TimelineProps,
} from "./types";

// Registro de composiciones. La duración, el fps y la resolución se derivan del
// timeline/EditProject real recibido como inputProps mediante `calculateMetadata`,
// de modo que el MP4 dura exactamente lo que marca el documento.
export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* Composición de GENERACIÓN (FEAT-005) — se conserva intacta. */}
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

      {/* Composición MULTIPISTA del editor (FEAT-003) — preview + export. */}
      <Composition
        id="Edit"
        component={EditComposition}
        durationInFrames={editDurationInFrames(defaultEditProps)}
        fps={defaultEditProps.fps}
        width={defaultEditProps.width}
        height={defaultEditProps.height}
        defaultProps={defaultEditProps}
        calculateMetadata={({ props }: { props: EditProps }) => {
          return {
            durationInFrames: editDurationInFrames(props),
            fps: props.fps,
            width: props.width,
            height: props.height,
          };
        }}
      />
    </>
  );
};

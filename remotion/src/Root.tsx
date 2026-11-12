import { Composition } from "remotion";

import { TimelineComposition } from "./TimelineComposition";
import { defaultTimelineProps } from "./types";

// Registro de composiciones. La duración/fps se derivarán del timeline real en FEAT-005.
export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Timeline"
      component={TimelineComposition}
      durationInFrames={90}
      fps={defaultTimelineProps.fps}
      width={defaultTimelineProps.width}
      height={defaultTimelineProps.height}
      defaultProps={defaultTimelineProps}
    />
  );
};

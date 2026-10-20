import { Composition } from "remotion";
import { DnsTrace, TOTAL_SEC } from "./DnsTrace";

const FPS = 30;

export const MyComposition = () => {
  return (
    <Composition
      id="DnsTrace"
      component={DnsTrace}
      durationInFrames={Math.ceil(TOTAL_SEC * FPS)}
      fps={FPS}
      width={1920}
      height={1080}
    />
  );
};

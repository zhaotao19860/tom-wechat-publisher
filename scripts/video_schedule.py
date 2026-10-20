#!/usr/bin/env python3
"""Turn narration clip lengths into the timeline a Remotion explainer plays.

Reads ``narration.json`` (one clip length per step, from video_narrate.py) and
writes ``schedule.json``:

    {
      "TOTAL": 165.4,
      "NARR_LEAD": 0.15,
      "SCENES": [{"start": 0, "end": 82.1}, ...],
      "SCHEDULE": [{"arrive": 1.0, "leave": 9.9, "scene": 0, "step": 0}, ...],
      "sfx": [{"kind": "click", "t": 1.0, "gain": 0.35}]
    }

Each step lasts ``max(min_step, clip + lead + tail)`` seconds, so the picture
never moves on while the voice is still talking. Every scene opens with
``--intro`` seconds of still picture and closes with ``--hold`` (``--outro`` on
the last scene). The Remotion composition imports this file to size every step,
and video_render.py places ``t<i>.wav`` at ``arrive + NARR_LEAD``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(durations: list[float], scenes: list[int], intro: float, hold: float, outro: float,
          lead: float, tail: float, min_step: float, click: bool) -> dict:
    if sum(scenes) != len(durations):
        raise SystemExit(f"--scenes adds up to {sum(scenes)} steps but narration has {len(durations)} clips")
    t = 0.0
    i = 0
    out_scenes, schedule, sfx = [], [], []
    for k, count in enumerate(scenes):
        start = t
        t += intro
        for j in range(count):
            length = max(min_step, durations[i] + lead + tail)
            schedule.append({"arrive": round(t, 3), "leave": round(t + length, 3), "scene": k, "step": j})
            if click:
                sfx.append({"kind": "click", "t": round(t, 3), "gain": 0.35})
            t += length
            i += 1
        t += outro if k == len(scenes) - 1 else hold
        out_scenes.append({"start": round(start, 3), "end": round(t, 3)})
    return {"TOTAL": round(t, 3), "NARR_LEAD": lead, "SCENES": out_scenes, "SCHEDULE": schedule, "sfx": sfx}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("narration", type=Path, help="narration.json from video_narrate.py")
    parser.add_argument("--out", type=Path, required=True, help="schedule.json the composition imports")
    parser.add_argument("--scenes", default="", help="steps per scene, e.g. 9,9 (default: one scene)")
    parser.add_argument("--intro", type=float, default=1.0)
    parser.add_argument("--hold", type=float, default=1.5, help="still time after a scene's last step")
    parser.add_argument("--outro", type=float, default=3.0, help="still time after the final step")
    parser.add_argument("--lead", type=float, default=0.15, help="narration starts this long after arrival")
    parser.add_argument("--tail", type=float, default=0.8, help="pause after a clip ends")
    parser.add_argument("--min-step", type=float, default=4.0, help="shortest step, room for the motion")
    parser.add_argument("--click", action="store_true", help="a soft click as each query leaves")
    args = parser.parse_args()

    durations = [float(d) for d in json.loads(args.narration.read_text(encoding="utf-8"))]
    scenes = [int(n) for n in args.scenes.split(",") if n.strip()] or [len(durations)]
    data = build(durations, scenes, args.intro, args.hold, args.outro, args.lead, args.tail, args.min_step, args.click)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(data['SCHEDULE'])} steps in {len(scenes)} scene(s), {data['TOTAL']:.1f}s -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

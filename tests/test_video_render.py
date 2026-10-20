from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("video_render", ROOT / "scripts/video_render.py")
video_render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video_render)
spec = importlib.util.spec_from_file_location("video_schedule", ROOT / "scripts/video_schedule.py")
video_schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video_schedule)


class VideoRenderTests(unittest.TestCase):
    def test_steps_follow_narration_and_scenes_get_intro_and_hold(self) -> None:
        data = video_schedule.build([2.0, 5.0, 1.0], [2, 1], intro=1.0, hold=1.5, outro=3.0,
                                    lead=0.15, tail=0.8, min_step=4.0, click=True)
        steps = data["SCHEDULE"]
        self.assertEqual(steps[0]["arrive"], 1.0)
        self.assertEqual(steps[0]["leave"], 5.0)  # short clip padded to min_step
        self.assertEqual(steps[1]["leave"], round(5.0 + 5.95, 3))
        self.assertEqual(data["SCENES"][0], {"start": 0.0, "end": round(10.95 + 1.5, 3)})
        self.assertEqual((steps[2]["scene"], steps[2]["step"]), (1, 0))
        self.assertEqual(steps[2]["arrive"], round(12.45 + 1.0, 3))
        self.assertEqual(data["TOTAL"], round(13.45 + 4.0 + 3.0, 3))
        self.assertEqual([fx["t"] for fx in data["sfx"]], [s["arrive"] for s in steps])

    def test_scene_counts_must_match_clips(self) -> None:
        with self.assertRaises(SystemExit):
            video_schedule.build([1.0, 1.0], [3], 1, 1, 1, 0.15, 0.8, 4, False)

    def test_narration_starts_after_arrival_and_sfx_follow_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp)
            (audio / "t0.wav").write_bytes(b"")
            (audio / "t1.wav").write_bytes(b"")
            schedule = {
                "TOTAL": 10,
                "NARR_LEAD": 0.2,
                "SCHEDULE": [{"arrive": 0.4}, {"arrive": 5.0}],
                "sfx": [{"kind": "ok", "t": 6.05, "gain": 0.35}],
            }
            events = video_render.audio_events(schedule, audio, audio)
            self.assertEqual([round(t, 2) for _, t, _ in events], [0.6, 5.2, 6.05])
            self.assertEqual(events[2][0].name, "ok.wav")
            graph = video_render.mix_filter(events, 10)
            self.assertIn("adelay=600:all=1", graph)
            self.assertIn("amix=inputs=3:normalize=0", graph)
            self.assertIn("atrim=0:10.000", graph)

    def test_unknown_sfx_kind_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            video_render.audio_events({"SCHEDULE": [], "sfx": [{"kind": "boom", "t": 1}]}, Path("."), Path("."))

    def test_sfx_files_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video_render.write_sfx(Path(tmp))
            for kind in video_render.SFX_KINDS:
                with wave.open(str(Path(tmp) / f"{kind}.wav")) as w:
                    self.assertEqual(w.getframerate(), video_render.SR)
                    self.assertGreater(w.getnframes(), 1000)


if __name__ == "__main__":
    unittest.main()

# Video explainer workflow

Use this when an article is better told with a short narrated animation: a protocol exchange, a resolution chain, a cache hit, a failover. The picture is a Remotion composition (React, rendered frame by frame to MP4), narrated with edge-tts and mixed with ffmpeg. The article still goes through every gate in `SKILL.md`; the video is one more asset, and a silent GIF of it goes in the body.

Reference project: `~/Desktop/wechat/remotion-dns/` (two DNS recursive resolutions, www.baidu.com cold cache and www.qq.com warm cache, 18 steps, about 3 minutes). A clean copy without `node_modules` lives in `assets/remotion-explainer-template/`. The older Anatomy pipeline (`~/Desktop/wechat/anatomy-dnssec/`) is retired: its isometric machines were dark, carried no packet labels and could not lay nodes out as a readable sequence.

## 1. Facts and steps first

Collect the real values before drawing anything (for DNS, `dig +trace` and `dig +norec @server` output: NS sets, glue, CNAME targets, TTLs, addresses). Split the story into steps. Each step gets:

- a short on-screen subtitle (written for reading),
- one spoken line (written for listening, see section 3),
- the packet text on the query and on the reply, and what lights or is written to a cache.

Put all of this in one data module (`src/steps.ts` in the template). Every number in the packet labels, subtitles, narration and article must come from the same collected data, and the module header records when and how it was collected.

## 2. Build the picture with Remotion

Load the `remotion-best-practices` and `remotion-markup` skills. Start from the template (`cp -R assets/remotion-explainer-template <dir> && cd <dir> && npm i`) or scaffold with `npx create-video@latest --yes --blank --no-tailwind <dir>`. The layout that tested well for protocol flows:

- **White background, high contrast.** Ink `#1F2937` on `#F7F8FA`, one colour per message kind (query blue, referral orange, authoritative answer green, priming purple). The legend sits in the header.
- **Actors as columns.** Client on the left, the component doing the work in the middle (for DNS, the resolver with zonedb, cachedb and adb), the servers it talks to stacked vertically on the right in the order they are asked. A server asked twice appears twice.
- **One exchange per step.** The query arrow draws out, the target lights, the reply arrow draws back. Each arrow carries a label pill with the real packet content (`www.baidu.com/A`, `referral → com. NS a~m.gtld-servers.net`, `AA CNAME …`). Pills are left-aligned next to the middle box and wrap rather than run into the server column.
- **History stays, focus moves.** Finished exchanges fade to grey lines; numbered badges stay on visited servers; only the current step's labels are shown.
- **State you can read.** Show what the actor learned (cache rows that appear on each reply, a highlight when a cached entry is reused) and what it is currently working on (the name card, with a tag such as `CNAME 重启`).
- **Subtitle bar** at the bottom with `n / N` and the subtitle.

Drive everything from `useCurrentFrame()` and `interpolate()`. No CSS transitions or animations; they do not render. Step timing comes from `src/schedule.json` (section 3), never from hard-coded seconds, so a step lasts as long as its narration.

Check before rendering: `npx tsc --noEmit`, then `npx remotion still <Comp> out/s.png --frame=N` at several step times in every scene. Read the PNGs: nothing overflows its box, no pill covers a server, the last cache row fits. `npm run dev` opens Studio for live preview.

## 3. Narration with edge-tts

The macOS `say` voices sound mechanical. Use edge-tts with a neural voice, pinned in a venv:

```bash
python3 -m venv .venv && .venv/bin/pip install edge-tts==7.3.1
export TOM_WECHAT_EDGE_TTS=$PWD/.venv/bin/edge-tts
```

edge-tts sends the text to Microsoft's online speech service. Only narrate text that will be published anyway.

Write `audio/lines.txt`, one spoken line per step, as a person explaining at a desk:

- open with where we are ("先看第一条""到了 com""再看第二条"), then what happens, then the result;
- use commas for breath; keep a step to one or two sentences;
- read identifiers the way people say them: `.com` as `点 com`, addresses digit by digit (`二二零点一八一点一一一点二三二`); keep short English terms (NS, TTL, BIND9, glue) as they are; replace long random labels (`ins-r23tsuuf`) with a description in the voice and leave the exact string to the picture;
- never put a number in the voice that differs from the picture.

Generate clips, then turn their lengths into the timeline:

```bash
python3 ~/.comate/skills/tom-wechat-publisher/scripts/video_narrate.py audio/lines.txt \
  --out-dir audio --durations narration.json
python3 ~/.comate/skills/tom-wechat-publisher/scripts/video_schedule.py narration.json \
  --out src/schedule.json --scenes 9,9 --click
```

`video_narrate.py` writes `audio/t<i>.wav` (default voice `zh-CN-YunxiNeural`, rate `+4%`). `video_schedule.py` makes each step `max(4.0, clip + 0.15 + 0.8)` seconds, adds a 1 s intro per scene, 1.5 s hold between scenes and 3 s at the end, and writes `TOTAL`, `SCENES`, `SCHEDULE` and optional click effects. The composition imports this file for its duration and per-step timing. Other voices: `zh-CN-XiaoxiaoNeural` (warm female), `zh-CN-YunjianNeural` (energetic), `zh-CN-YunyangNeural` (newsreader).

## 4. Render and encode

```bash
python3 ~/.comate/skills/tom-wechat-publisher/scripts/video_render.py <project> \
  --composition DnsTrace --schedule src/schedule.json --audio-dir audio \
  --out out/video.mp4 --gif images/bodyN-video.gif --gif-speed 3 --gif-fps 5
```

It runs `npx remotion render --muted`, mixes narration and effects at `arrive + NARR_LEAD` with loudness normalisation (−16 LUFS), encodes 1280-wide H.264 and AAC, and writes an optional silent GIF from the silent render. WeChat rejects GIFs with more than 300 frames, so keep `TOTAL / speed × fps ≤ 300`. Omit `--audio-dir` for a silent video.

Check the result:

- `ffprobe` shows one video and one audio stream of the same length;
- extract a frame at a few step times and confirm the subtitle matches what is spoken then;
- `volumedetect` mean around −20 dB, max below −1 dB;
- ask the user to listen. You cannot judge the voice yourself, so do not claim it sounds natural.

## 5. Put it in the article

- When the user wants the video first, open the article with one short paragraph that points to the narrated video at the very top and leave the slot for it there. The Ian Xiaohei image is still the first body image. The silent GIF goes later in the body with a caption.
- Article facts about the video (duration, step count, voice) must match the final file.
- **Do not upload the MP4 with `material/add_material`.** Videos uploaded through the API appear in the material library but are greyed out in the editor's "选择视频" dialog and cannot be embedded in a 图文 body. The user clicks 「本地上传」 in the mp.weixin.qq.com editor, waits for transcoding and inserts it at the slot. Their logged-in web session is required, and the agent cannot do this step.
- Tell the user the local video path and where to insert it. Creating the draft follows the normal `--upload` rules in `SKILL.md`.
- Re-uploading creates a new draft. Warn the user that edits made in the web editor on an older draft do not carry over.

---
name: tom-wechat-publisher
description: Generate, polish, illustrate, prepare, validate, preview, and explicitly upload WeChat Official Account draft articles. Trigger whenever the user asks to turn a URL, web announcement, local file, meeting notes, conversation, summary, or topic brief into a WeChat article, or mentions 公众号, 微信公众号, 草稿箱, Markdown/md 公众号排版, 草稿上传, 封面处理, 正文图片兼容, 公众号预览, or WeChat media errors such as 40113, or asks for a 视频讲解/讲解视频/配音动画 to accompany an article. It supports source-grounded article generation, direct conversation/context-to-article workflows, local Markdown preparation, multi-account profiles, AI Chinese-painting covers for technical accounts, clinical-illustration covers and body images for the medical account, deterministic factual infographics, and draft-only upload. Do not trigger for standalone illustration generation, GitHub Pages publishing, or mass sending. This skill may create drafts only after explicit user authorization; it must never publish or mass-send.
---

# WeChat Draft Publisher

## Purpose

Use this skill to turn a URL, local source file, conversation, meeting note, summary, or topic brief into a grounded WeChat article, or to prepare an existing Markdown article. The workflow can generate and inspect the article's cover and body illustrations, render the exact themed output with the installed Wenyan CLI, and optionally create a WeChat draft through Wenyan's draft-only publishing path.

The default workflow stops after preparation and preview. Creating a draft is a side effect and requires explicit user authorization in the current request.

Use `赵涛` as the default article author. Resolve author precedence as `--author` > `WECHAT_AUTHOR` > account profile author > `赵涛`, so explicit per-article, environment and per-account overrides continue to work.

## Account Profiles

The skill supports more than one WeChat Official Account. Each account is a JSON profile in `accounts/`; `accounts/medical.json` is the independent medical account. Never share one account's credentials, history root or album with another account.

- Select a profile with `--account medical` or `TOM_WECHAT_ACCOUNT=medical`. Omitting both keeps the built-in technical account, so existing DNS/gateway workflows are unchanged.
- A profile isolates the AppID/AppSecret **variable names** (never the secret values), Keychain service, author, default theme, history root, album, footer image, image style, preview themes and default content track. The medical profile uses `WECHAT_MEDICAL_APP_ID` / `WECHAT_MEDICAL_APP_SECRET` and `tom-wechat-publisher-medical`.
- `publish_draft.sh` resolves the profile after argument parsing and prints `Account profile: ...`; before upload it prints `Target account: ...` and the masked AppID. Treat a wrong target account as a hard stop.
- A dedicated profile history root is created on first generate-mode run so medical topic de-duplication never mixes with technical articles.
- `accounts/*.json` must contain no secrets. Store credentials in environment variables or the account's Keychain service.
- Optional `voice` and `voice_facts` name the account's voice card and confirmed first-person fact list. They usually live outside the repo, for example `~/.tom-wechat-autopilot/accounts/dns/voice.md` and `voice-facts.md`; `~` is expanded and relative paths resolve against `accounts/`. A path that is set but missing fails closed. They are exported as `PROFILE_VOICE` / `PROFILE_VOICE_FACTS`, and `voice_facts` feeds `voice_check.py --facts`.
- Optional `max_han` (integer ≥ 0, default unset) is the account's body Han-character cap for `voice_check.py`'s `too_long` fail; unset means `TOM_WECHAT_MAX_HAN`, else 1500, and 0 disables the cap. It is exported as `PROFILE_MAX_HAN` and passed as `--max-han`.
- Optional `image_backend` (`comate`, `openai`, `gemini`, `command`) picks that account's AI image backend. `accounts/medical.json` leaves it empty, which means the env var or the `comate` default decides. See 图片后端.

## Scope

This skill can:

1. Generate a grounded article from a URL, local text/Markdown source, conversation context, meeting notes, summary, or topic brief.
2. Enrich source material with optional web search and preserve uncertainty instead of inventing facts.
3. Write and revise through the `human-writing` skill so the article reads like a person who checked the material, not a model report.
4. Generate and route the cover and body illustrations according to factual risk.
5. Normalize Markdown metadata and local image references.
6. Convert unsupported local images into WeChat-compatible formats.
7. Validate the article, cover, and body images before upload.
8. Render one or more local HTML previews with bundled themes.
9. Create a WeChat draft through an explicitly configured upload command.
10. Diagnose failures by stage: source ingestion, fact extraction, prose revision, image generation, preflight, preparation, rendering, media upload, or draft creation.
11. Produce a narrated explainer video for the article: a Remotion animation, edge-tts narration timed per step, and an MP4 plus a silent body GIF (see `references/video-explainer.md`).

This skill must not:

- Publish or mass-send a WeChat article.
- Upload when the user only asked to write, polish, convert, inspect, or preview.
- Ship prose that fails the `human-writing` revision gate.
- Pad an article with restated points, invented examples, or hypothetical people when material is short.
- Claim upload success from an exit code alone when the adapter output reports failure.
- Generate article illustrations itself when a dedicated illustration skill is a better fit.
- Handle GitHub Pages publishing; use `tom-pages-publisher` for that.
- Upload an article video through `material/add_material` and call it embedded. API-uploaded videos cannot be inserted into a 图文 body; the user must use 「本地上传」 in the web editor.

## Safety Contract

Treat draft creation as an external side effect.

Before upload:

1. Confirm the user explicitly requested creation of a WeChat draft.
2. Run preparation and preview first.
3. Report the resolved title, author, cover, body image count, theme, and target account when the adapter exposes it.
4. Stop on missing files, unsupported images, failed conversion, failed rendering, missing credentials, or ambiguous account selection.
5. Use `--upload` only after the above checks pass.

Never reinterpret “预览”, “转换”, “排版”, “检查”, “润色”, or “先不要上传” as upload authorization.

## Quick Workflow

For a gateway article, set `track: l4-gateway` or `track: ai-gateway` in the frontmatter before preparing it (generate mode: `--track`). DNS articles need no change. See Content Tracks.

### Prepare and preview

```bash
bash ~/.comate/skills/tom-wechat-publisher/scripts/publish_draft.sh \
  /absolute/path/article.md \
  --title "Article title" \
  --cover /absolute/path/cover.jpg \
  --author "赵涛" \
  --theme dns-sakura \
  --require-body-images \
  --preview-dir /absolute/path/preview \
  --no-open-preview
```

This is the default and performs no upload. `--preview-dir` keeps `preview.html`
and all referenced local images outside the temporary staging directory. Persistent
previews are read and written as UTF-8, and the saved HTML declares
`<meta charset="utf-8">` so Chinese text remains legible when reopened.

### Compare themes

```bash
bash ~/.comate/skills/tom-wechat-publisher/scripts/publish_draft.sh \
  /absolute/path/article.md \
  --preview-themes \
  --no-open-preview \
  --keep-temp
```

Use the selected theme only after the user confirms it. `dns-sakura` is a strong candidate for DNS policy and industry analysis, but it is not a universal default.

### Create a draft

Only after explicit authorization:

```bash
bash ~/.comate/skills/tom-wechat-publisher/scripts/publish_draft.sh \
  /absolute/path/article.md \
  --title "Article title" \
  --cover /absolute/path/cover.jpg \
  --author "赵涛" \
  --theme dns-sakura \
  --theme-selection-confirmed \
  --upload
```

The wrapper uses the installed Wenyan CLI for preview and draft creation. `wenyan render` and `wenyan publish` receive the same staged Markdown, custom theme CSS, and highlight theme. Wenyan handles UTF-8 input, local body-image upload, cover material upload, URL replacement, and WeChat `draft/add`.

The wrapper must return a non-zero exit code when Wenyan fails. Output containing a clear error response is treated as failure even when the exit code is zero.

### Reference links and footnotes

Wenyan's link-footnote endnote list (`--footnote`) is disabled by default in the wrapper. For this skill's workflow it hurt more than it helped: articles carry an explicit `## 参考资料` section, so the auto endnote duplicated every link, and links written as `[https://url](https://url)` (anchor text equal to the URL) rendered as `[n]undefined: https://url` plus a second bare entry. WeChat also strips clickable links from the body, so a plain-text `参考资料` section is the reliable way to expose URLs.

Keep references as a manual `## 参考资料` section with a short human label followed by the bare URL, for example `- octoDNS 仓库 https://github.com/octodns/octodns`. Do not wrap a URL in `[url](url)`. Pass `--footnote` only for an article that deliberately uses inline anchor-text links and has no manual reference section.

### Article footer

Technical accounts end with the shared footer image `~/Desktop/wechat/images/footer-guofeng-seal.png`, placed after `## 参考资料` as the last image of the article. The medical account may set `footer_image` in its profile; leave it empty to use no footer image rather than borrowing the technical account's seal.

```markdown
## 参考资料

- ...

![](../images/footer-guofeng-seal.png)
```

- Reference the shared file (relative `../images/...` from an article directory under `~/Desktop/wechat`); do not copy or regenerate it, so a footer update reaches every article.
- The footer is not a body illustration: it gets no entry in `image_specs.json` and does not count toward the 3-4 body slots. `validate_body_images.py` skips it, requires it to be the last image when present, accepts `--footer NAME` / `TOM_WECHAT_FOOTER_IMAGE`, and rejects the retired footers `wechat.jpg` / `wechat-sign.jpg`.
- This convention is for WeChat articles only; KU documents keep their own footer rules.

## Content Tracks

The accounts run four content lines. An article declares its line with an optional frontmatter field:

```yaml
---
title: "..."
track: ai-gateway
---
```

| `track` | Line | Image kicker | Album |
|---|---|---|---|
| `dns` | DNS | `DNS Daily` / `DNS DAILY` | DNS知识库 |
| `l4-gateway` | 四层负载均衡网关 (LVS/DPDK, VIP, ECMP, 会话保持, 健康检查) | `L4 GATEWAY` | 网关知识库 |
| `ai-gateway` | 七层 AI 网关 (模型路由, 限流, token 计费, fallback, OpenAI-compatible APIs, MCP) | `AI GATEWAY` | 网关知识库 |
| `medical` | 医学科普 (疾病、用药、剂量、禁忌、复查与就医提示) | `MED PRACTICE` / 临床笔记 | 医学科普库 |

- A missing or unknown `track` resolves to `dns`, and DNS output stays byte-identical to articles written before tracks existed. `track: medical` is required for the medical account; do not rely on keyword inference in the frontmatter.
- `scripts/tracks.py` holds the per-track display name, kicker, vocabulary, and album. `image_spec.py` copies `track` into `image_specs.json` and adds the track vocabulary to label extraction; `html_render.py` uses it for cover/kicker chrome and Chinese labels on deterministic diagrams.
- `publish_draft.sh --generate-from ... --track l4-gateway` writes the field into the generated frontmatter. The `medical` account profile supplies `medical` as the default track when `--track` is omitted.
- The draft API cannot assign an album. After a draft upload, and at the end of `--dry-run` / `--render-only`, the wrapper prints `Album reminder: 发表时请在后台选择合集「...」`. Pass it on to the user: the album must be picked by hand in the WeChat backend when publishing.
- `check_topic_history.py` reports each match's `track`, so a gateway topic that resembles a DNS article is easy to tell apart.

## Preflight Rules

The wrapper must verify:

- Python 3.9 or newer is available. `TOM_WECHAT_PYTHON` may select a specific executable.
- The source Markdown exists and is readable.
- Local Markdown and HTML image references resolve.
- The cover exists locally and the mandatory AI cover metadata contract is valid.
- Prepared images are JPEG, PNG, or GIF.
- High-risk HTML/hybrid/mermaid specs contain `locked_facts`, and body labels are article-specific.
- The delivered prose passed `human-writing/scripts/check_prose.py` after the last content edit.
- `scripts/voice_check.py` ran after check_prose.py (warn mode by default; see Enforced revision gate).
- Wenyan CLI is installed and executable for preview and upload.
- The `mermaid` npm package is installed at the skill root when any body spec uses `render_mode: mermaid` (`cd ~/.comate/skills/tom-wechat-publisher && npm install`).
- Upload is not attempted with `--dry-run`, `--render-only`, or theme-preview mode.

Use `--keep-temp` while diagnosing problems. Temporary output otherwise belongs under the system temporary directory and is removed on exit.
Failure notifications are disabled by default. Set `TOM_WECHAT_FAILURE_NOTIFY` to an explicit 如流 recipient to opt in. Sending needs the intranet `infoflow-message-group` skill under `~/.comate/skills`; when it is missing the wrapper logs `[notify] 如流通知跳过 (... infoflow=missing)` and keeps the original exit code.

## Mandatory Topic History Check

Before outlining or drafting any new article, choose a concrete working topic and compare it with previously written articles. This gate applies whether the input is a URL, local file, conversation, notes, summary, or topic brief.

```bash
python3 ~/.comate/skills/tom-wechat-publisher/scripts/check_topic_history.py \
  --topic "Concrete working topic" \
  --source "SOURCE_URL_OR_PATH" \
  --history-root "${TOM_WECHAT_HISTORY_ROOT:-$HOME/Desktop/wechat}"
```

Treat the report as a required input to topic selection:

- `CLEAR` (exit `0`): record that no material overlap was found, then start the outline.
- `RELATED` (exit `4`): read the listed historical titles, compare their core question, evidence, and conclusion, and state the new angle before drafting. Use `--topic-history-reviewed` only after this review.
- `DUPLICATE` (exit `3`): stop before drafting and show the user the matching title and path. Continue only when the user explicitly requests an update or sequel; define what has changed and use `--allow-topic-repeat`.
- Check failure (exit `2` or another unexpected code): stop. Do not treat a missing or unreadable history root as a clean result.

Changing only the headline, opening anecdote, examples, or visual style does not create a new topic when the core question and conclusion remain the same. For a distinct follow-up, identify a genuinely different audience, time window, evidence set, operational decision, or unresolved question.

The `--generate-from` wrapper enforces this gate before invoking `generate_article.py` and requires `--topic`. Set `TOM_WECHAT_HISTORY_ROOT` or pass `--history-root` when the archive is elsewhere.

## Mandatory Human Prose Gate

Every article this skill writes, rewrites, or polishes must go through the `human-writing` skill. Load it before outlining and apply its revision rules before preview. A technically correct article that reads like a model report is not an acceptable deliverable.

### Before drafting

1. Load `human-writing`, then load `references/forum-prose.md` and `references/reality.md` for source-grounded technical articles.
2. Count concrete material. A 1200-plus-character Chinese article needs at least five specific items with a traceable origin, and they must form one real process rather than five adjacent opinions.
3. When material is short, search first, then ask the user at most three questions, then shorten the article. Never pad with restated points, invented examples, or hypothetical people.
4. Plan for at most 1500 Han characters of body text (front matter, code blocks, image lines and everything from `## 参考资料` on are not counted; `TOM_WECHAT_MAX_HAN` or the account profile's `max_han` changes the cap, 0 removes it). Pick one main thread and drop the side topics before writing: two or three sections, little background, no history lesson the reader already knows. When a draft runs long, delete whole paragraphs or sections that are not on the main thread instead of squeezing every sentence.
5. Fix the speaking position. Decide what the author actually knows, what is inference, and where the judgment stops.
6. If the account profile or the caller supplies a voice card (for example `voice.md`) and a first-person fact list, load them after human-writing. The voice card overrides human-writing's default tone, its forum-prose examples, and its "no author profile" clause, because it is the user's explicit tone instruction. Only facts on the first-person list, or facts the user stated in this conversation, may be written with 我. Commands the agent ran are reported without 我.

### While drafting

- Open on a concrete situation, a direct answer, or one odd fact. Do not announce the article structure.
- Give every section a new fact, action, distinction, or consequence. Restating a point in new words is padding.
- Put the subject and action early, then attach time, cause, and condition.
- Let paragraph length vary. Reserve single-sentence paragraphs for real pauses.
- Keep judgments visible and place their evidence nearby.
- Stop when the point is finished. Do not add a closing summary, a symmetry callback, or an era-scale flourish.

### Hard bans in the delivered text

These cover the title, headings, body, image captions, and quoted material. One hit blocks preview and upload.

- No Chinese or English colons in prose. URLs, code, and unchangeable machine fields are exempt.
- No em dash, double dash, or en dash.
- No reversal templates such as 不是……而是……, 并非……而是……, 不在于……而在于……, 与其说……不如说……, 不只……还……, 表面……实际……, 看似……实则…….
- No 不丢, 说白了, 说穿了, 先说结论.
- No insight signposts used to inflate a paragraph, such as 更微妙的是, 还有一层, 值得注意的是, 需要指出的是, 从某种意义上说.
- No business or model jargon in place of people, actions, money, time, and consequences. Follow the hard-banned list in `human-writing/references/revision.md`.
- No borrowed-world metaphors such as 仓库, 抽屉, 温度, 死亡, 坍塌, 浪潮, 钥匙, 底座 wrapping abstract concepts in non-fiction.

### Enforced revision gate

After the Markdown is finalized and before rendering the preview:

1. Before running check_prose.py, run one harsh-editor pass in a fresh context using `references/harsh-editor.md`. Give that context only the article, the voice card, the first-person fact list and `qa.md`, never the drafting history.
2. Run check_prose.py, then voice_check.py:

```bash
python3 ~/.codex/skills/human-writing/scripts/check_prose.py /absolute/path/article.md
python3 -I ~/.comate/skills/tom-wechat-publisher/scripts/voice_check.py /absolute/path/article.md \
  --facts /absolute/path/voice-facts.md
```

`voice_check.py` counts what check_prose.py cannot see. Fails (需要修改) are stock frames such as 我原本以为 / 写得很硬 / 到这里为止, conclusion-style H2 such as 「A，B 才 C」, first-person claims with no possible source such as 我第一件事就是 / 我见过的人, and `too_long`: a body over the Han-character cap (`--max-han`, else the profile's `max_han` passed by the wrapper, else `TOM_WECHAT_MAX_HAN`, else 1500; 0 disables). Warns (需要人工判断) are first-person experiences that no line of the fact list covers, sentence-length CV below 0.55, section-length CV below 0.45, more than 4 H2, no question mark in an article over 1200 Han characters, more than half of the paragraphs closing on a short verdict, dense 、 lists, and glossing terms for peers. Add `--json` for a machine-readable report. Standalone it defaults to `--mode block` (exit 1 on fails); the wrapper passes the mode from `TOM_WECHAT_VOICE_CHECK`.

The wrapper runs voice_check.py right after check_prose.py:

- `TOM_WECHAT_VOICE_CHECK=warn` (default) prints findings and continues, `block` stops on fails, `off` skips the check. Any other value is an error.
- `--facts` comes from `TOM_WECHAT_VOICE_FACTS`, else from the account profile's `voice_facts`. Without a fact list every first-person experience line is a warn.

The wrapper runs this checker before preview and upload. It looks for `human-writing/scripts/check_prose.py` under `~/.codex/skills`, `~/.agents/skills`, `~/.comate/skills` and `~/.claude/skills`, in that order. On a machine that has none of them (for example a home Mac running Claude Code), copy the whole `human-writing` skill directory from a machine that has it, e.g. `rsync -a work-mac:.codex/skills/human-writing ~/.claude/skills/`, or set `TOM_WECHAT_CHECK_PROSE` to an executable checker path; the override is tried first. A missing checker or non-zero result blocks the workflow. Run the gate again after every content edit, including edits between preview and upload.

Treat a non-zero exit as a blocking failure and keep revising until the banned items reach zero. Context-dependent warnings still need a manual pass. When the script flags mechanical listing, uniform paragraph shape, repeated signposts, or stacked metaphors, rewrite rather than dismiss it.

Run this gate again after any content edit, including edits requested between two uploads. Do not reuse an earlier passing result for changed text.

Two failure patterns deserve specific attention in this skill's output.

- Numbered enumeration replacing prose. A section written as 第一项 through 第七项 should become grouped narrative that explains why the items belong together, while keeping every locked fact intact.
- A closing paragraph that summarizes the whole article. Delete it and let the last concrete judgment end the piece.

When polishing an existing article, preserve its facts, dates, versions, product names, causal wording, and stated uncertainty. Rewriting for voice must not change what the article claims.

## Optional Content Tools

The bundled wrapper also supports article generation and humanization. These are optional preprocessing paths, not prerequisites for draft preparation.

```bash
bash ~/.comate/skills/tom-wechat-publisher/scripts/publish_draft.sh \
  --generate-from SOURCE \
  --out-dir /absolute/path/output \
  --topic "Topic"
```

```bash
bash ~/.comate/skills/tom-wechat-publisher/scripts/publish_draft.sh \
  --humanize /absolute/path/article.md \
  --score
```

`--generate-from` no longer runs humanize by default; pass `--humanize` to run it on the generated article. Humanize no longer inserts 这一点很重要。 or trims 「A、B和C」 lists.

Do not invent facts while polishing. Preserve source facts, causal wording, uncertainty, dates, product names, and quoted claims.

## Optional Video Explainer

When the user asks for a 视频讲解 / 讲解视频 / 动画视频 to go with an article, use this pipeline and read `references/video-explainer.md` first:

1. Collect the real facts and split the story into steps (subtitle, spoken line, packet text on query and reply, what lights or is cached). Keep them in one data module.
2. Build the picture as a Remotion composition, starting from `assets/remotion-explainer-template/` and loading the `remotion-best-practices` / `remotion-markup` skills. White background, actors as columns, servers stacked vertically, a labelled arrow pair per exchange, cache state on screen, subtitle bar at the bottom. Animate only with `useCurrentFrame()` + `interpolate()` and read step timing from `src/schedule.json`. Check stills at several steps before rendering. Anatomy is no longer used for videos.
3. Narrate with edge-tts (default `zh-CN-YunxiNeural`, never macOS `say`). Write the lines in spoken Chinese, generate the clips, then turn their lengths into the timeline so each step fits its narration.

   ```bash
   python3 ~/.comate/skills/tom-wechat-publisher/scripts/video_narrate.py audio/lines.txt --out-dir audio --durations narration.json
   python3 ~/.comate/skills/tom-wechat-publisher/scripts/video_schedule.py narration.json --out src/schedule.json --scenes 9,9 --click
   ```

4. Render, mix and encode with `scripts/video_render.py`. It renders the composition silent with Remotion, mixes the narration on the schedule's clock, and writes the MP4 and a silent GIF for the body (keep the GIF at or under 300 frames).

   ```bash
   python3 ~/.comate/skills/tom-wechat-publisher/scripts/video_render.py /path/to/remotion-project \
     --composition DnsTrace --schedule src/schedule.json --audio-dir audio \
     --out video.mp4 --gif images/bodyN-video.gif --gif-speed 3 --gif-fps 5
   ```

5. Put the GIF in the body, keep every video fact in the article in sync with the final file, and tell the user to insert the MP4 with 「本地上传」 in the editor. When the video should open the article, leave its slot above the first Ian image and say so in the opening paragraph.

edge-tts sends the narration text to Microsoft's online service, so only narrate publishable text. Install it pinned (`edge-tts==7.3.1`) in a venv and point `TOM_WECHAT_EDGE_TTS` at it. You cannot hear the result, so ask the user to listen before calling the voice done.

## Mandatory AI Cover

Every article prepared by this skill must use a genuinely AI-generated cover. Technical accounts use a Chinese painting style; the medical account uses a clinical illustration style. This is a hard requirement, not a visual suggestion.

### Medical account covers

`track: medical` switches the cover contract:

1. Allowed styles are `临床插画`, `医学水彩`, and `临床信息图`. Cycle them with `python3 scripts/next_cover_style.py --track medical --history-root ~/Desktop/wechat-medical`.
2. Use a calm clinical editorial illustration: restrained line-and-wash or flat vector, muted teal/sage/warm-neutral palette, anonymous non-identifiable people (if any), one everyday-care or clinical metaphor, generous negative space. No gore, surgery close-up, identifiable patient, hospital logo, diagnostic image presented as fact, guohua mountains, or SaaS dashboard.
3. Do not auto-generate a poem. `cover_poem` is optional and may be omitted or empty. If the user supplies an inscription, it may contain 2 or 4 lines and should be set as a quiet printed/semi-brush inscription, not guohua calligraphy.
4. `cover_typography` may be `none` when there is no inscription; otherwise use `vertical`, `horizontal`, or `split`.
5. Keep all readable text out of the cover by default. Never render drug names, doses, lab values, or patient identifiers.

Example:

```yaml
cover: "images/cover-medical.png"
cover_generation: "ai"
cover_style: "临床插画"
cover_typography: "none"
```

### Technical account covers

Before generating the cover:

1. Select exactly one style for the current article. If the user names a style or is actively choosing the visual direction, honor that. Otherwise **cycle through the ordered list in step 2 in sequence**: use the style that immediately follows the most recently used cover style, and wrap back to the first style after the last. This round-robin keeps consecutive articles from repeating a style until the whole list has been used, and it avoids the landscape bias of free judgment. Determine the next style with the bundled helper, which scans the history root for the last used `cover_style` and prints the one that comes next in the cycle:

   ```bash
   python3 ~/.comate/skills/tom-wechat-publisher/scripts/next_cover_style.py \
     --history-root "${TOM_WECHAT_HISTORY_ROOT:-$HOME/Desktop/wechat}" --verbose
   ```

   Report which style was chosen and which one it followed. Any style in the list is acceptable for any topic.

2. Allowed styles, in cycle order, are: `水墨写意`, `工笔重彩`, `青绿山水`, `宋画风`, `敦煌壁画风`, `文人画`, and `新中式国风`. This exact order defines the round-robin sequence in step 1, and it is kept in sync with `DEFAULT_STYLES` in `scripts/next_cover_style.py`. The cover scene and focal subject should suit the chosen style (for a flower-and-bird 花鸟, an interior, a figure, or an object composition, do not force a landscape), while still expressing the article's concept.
3. Generate the cover through `scripts/image_backend.py` (same CLI as the `create-image` wrapper, which stays the default backend; see 图片后端). The cover must be genuinely AI-generated.
4. Use a 2.35:1 composition suitable for a WeChat headline cover. Generate at 16:9 when the image API does not support 2.35:1, then crop deliberately to 1800x766 while preserving the focal subject. Use the bundled helper; it needs only macOS `sips`, so do not reach for Pillow (it is not installed in the system Python and `pip install` is blocked by PEP 668):

   ```bash
   python3 ~/.comate/skills/tom-wechat-publisher/scripts/cover_image.py crop images/cover-raw.png \
     --out images/cover-ai-poem.png --anchor top      # top | center | bottom | 0..1
   python3 ~/.comate/skills/tom-wechat-publisher/scripts/cover_image.py check images/cover-ai-poem.png
   ```

   Pick the anchor that keeps the poem and focal subject inside the frame, keep the raw generation as a separate file, and look at the cropped result before moving on. `check` exits non-zero unless the cover is 1800x766 (or 2.35:1 at least 1800 wide).
5. Store provenance in frontmatter:

```yaml
cover: "images/cover-ai.png"
cover_generation: "ai"
cover_style: "工笔重彩"
```

Do not use HTML/CSS rendering, charts, screenshots, stock images, old article assets, or deterministic diagrams as the article cover. HTML-rendered images are allowed only for label-heavy body infographics.

The cover should express the article concept through Chinese visual language rather than imitate a generic cybersecurity poster.

### Poetic inscription (technical accounts)

Every new or regenerated technical cover must include an original four-line Chinese poetic inscription derived from the article. The medical account is exempt: its inscription is optional and must be user-supplied. All four lines must use one consistent length, either exactly 5 or exactly 7 Han characters per line. The four lines should follow this shape: scene, tension, action, consequence. This is a generation contract, not a request for a title paraphrase.

1. Write the poem after the article is finalized. Extract the article's central image, tension, and consequence rather than paraphrasing its title.
2. Keep the language concise and classically inflected, but understandable. Do not force technical terms, product names, English, CVE/GHSA identifiers, or marketing slogans into the poem.
3. Check that the poem does not invent factual claims. It is a visual metaphor, not evidence or a technical summary.
4. Generate the poem first, then generate the Chinese painting, calligraphy, seal, negative space, and focal subject together in one AI composition. The inscription must feel painted into the same visual world, like a traditional title inscription, not pasted on afterward.
5. Prefer integrated horizontal or vertical calligraphy placed directly in natural mist, sky, water, paper texture, or other calm negative space. Match ink density, brush texture, scale, rhythm, and seal placement to the painting.
6. Explicitly forbid independent text panels, floating cards, opaque banners, rectangular paper strips, UI-like containers, and visibly composited backing blocks. Do not reproduce the separated 'poem pasted over painting' look.
7. After the first generation, verify every character and the overall integration. The rendered poem must exactly match the approved lines and contain no quotation marks, book-title marks, brackets, commas, periods, decorative punctuation, or extra characters unless the user explicitly approved them. Treat surrounding `“”`, `‘’`, `「」`, `『』`, `《》`, or sentence-ending punctuation as errors. If the poem has a wrong character, extra punctuation, missing line, bad spacing, or weak visual unity, use image-to-image editing with the generated cover as input and request a localized correction while preserving the painting.
8. Repeat image-to-image repair when useful. Use deterministic text compositing only as the final fallback when image-to-image cannot produce correct text. Even in fallback mode, text must sit directly in existing negative space with transparent treatment; never add a separate panel or banner.
9. Preserve mobile legibility without making the poem dominate the painting. The title inscription, the painting's own compositional rhythm, and the red seal should form one balanced composition.

Record the final inscription and composition in frontmatter:

```yaml
cover: "images/cover-ai-poem.jpg"
cover_generation: "ai"
cover_style: "水墨写意"
cover_poem:
  - "景起一线入长空"
  - "疑云压岸动秋声"
  - "执灯寻证过深巷"
  - "回望方知路已明"
cover_typography: "vertical"
```

`cover_poem` for a new or regenerated cover must contain exactly 4 non-empty lines, and all four lines must have the same length of exactly 5 or exactly 7 Han characters. The validator retains a two-line path only for an unchanged historical article whose existing cover and poem are being preserved for compatibility; do not use that path for generation or regeneration. `cover_typography` must be one of `vertical`, `horizontal`, or `split`.

HTML/CSS text compositing is a last-resort fallback only after at least one integrated generation and one image-to-image repair attempt fail. It must never replace the AI painting and must never introduce a visible panel, banner, card, or backing rectangle.

Before preview or upload, stop with an error if the cover is missing, `cover_generation` is not `ai`, `cover_style` is outside the track's allowed list, a technical-account poem does not contain exactly 4 same-length lines of 5 or 7 Han characters, or typography is invalid. A medical-account poem may be absent or contain 2/4 lines, and `cover_typography: none` is valid when absent. An unchanged historical article may retain exactly 2 lines under the explicit compatibility path. Do not silently accept a technically valid but non-compliant new cover.

## Hybrid Body Illustration Routing

Body illustrations must use a content-risk routing process inspired by material-editorial illustration workflows. Do not default every slot to AI and do not default every slot to HTML.

### Extract cognitive anchors

After the article is finalized, identify only the concepts that materially reduce reader effort:

- the central scene or mechanism;
- prerequisites and exposure conditions;
- ordered processing or failure path;
- exact data, versions, configuration, or code identifiers;
- operational action or decision.

Do not illustrate every heading. A normal technical article should use 3-4 body images with different information structures.

For technical articles, make the first body image an editorial visual anchor: place one `ai` + `low` Ian Xiaohei illustration before the first `##` heading. 小黑 must perform the article's central action on a pure-white hand-drawn scene. Put exact versions, CVE identifiers, commands, tables, and checklists in later deterministic HTML or hybrid images. `validate_body_images.py` rejects any technical article whose first body image is not an Ian Xiaohei `ai` + `low` slot.

For the medical account, the first body image must **not** use Ian Xiaohei. Use a clinical editorial illustration (`illustration_backend: clinical-illustration`, anonymous figures, no cartoon, no readable drug names/doses) or a deterministic opener. `validate_body_images.py` rejects an Ian opener on `track: medical`.

### Choose a visual structure

Select the structure from the content rather than asking the user to pick a mode:

- `pipeline`: ordered flow, attack path, processing path;
- `layer-stack`: protocol or architecture layers;
- `hub-and-spoke`: one component with several inputs or dependencies;
- `before-after`: state transition, failure, migration, or remediation;
- `data-first`: exact metrics, versions, matrices, or comparisons;
- `text-scene`: editorial scene or metaphor for an abstract concept;
- `checklist`: operational checks and actions.

### Route by fact risk

Assign every image one `render_mode` and one `fact_risk`. Pick whatever backend
fits the content; never default every slot to one backend. The goal for every
body image is dense information, a sensible layout, and no large blank band.

- `ai` + `low`: for technical tracks, delegate the complete body illustration to `ian-xiaohei-illustrations`. Use Ian's pure-white, minimalist black hand-drawn visual DNA; 小黑 must perform the core conceptual action. Use no text or at most 3-5 short Chinese labels, and run Ian's QA checklist. Ian invokes `create-image` as its image backend. For `track: medical`, use `clinical-illustration`: a calm clinical editorial illustration, anonymous figures, no cartoon mascot, text-free, and no anatomy presented as a diagnostic image.
- `mermaid` + `medium/high`: ordered flows, relationships, comparisons, protocol layers, and timelines. This is the preferred backend for structural/relational content. The mermaid backend auto-lays-out the graph and captures it full-page at WeChat width, so there is no fixed 16:9 canvas and no large blank band. Every visible label comes straight from the spec, so exact facts stay verbatim (deterministic, not AI-guessed) — mermaid is fact-safe even at `high` risk.
- `html` + `high`: genuine numeric series (5-10 comparable values on a bar chart) and dense tables where a chart fills the canvas. Render all facts deterministically.
- `hybrid` + `medium/high`: mechanism, architecture, or attack path that needs an AI visual base plus exact labels. Do not delegate locked facts to Ian or any free-form image prompt.

Prefer `mermaid` over the fixed 16:9 Swiss layouts for flows, comparisons, timelines, and relationship diagrams: the Swiss layouts assume slide-density content and leave large blank areas when the spec is sparse. Keep `html` for numeric bar charts (which fill their canvas) and reserve `hybrid` for slots that genuinely need an AI base.

For low-risk AI body slots on technical tracks, `tom-wechat-publisher` owns article selection and fact-risk classification, while `ian-xiaohei-illustrations` owns the shot metaphor, 小黑 action, prompt construction, `create-image` invocation, and Ian QA. For `track: medical`, the skill owns the clinical-illustration prompt and the slot must stay text-free. The cover follows the account's cover workflow (guohua for technical, clinical illustration for medical). The image model must not infer or rewrite exact data.

### Mermaid backend

Structural/relational body images are rendered by `html_render.py` through the bundled mermaid renderer:

- `image_spec.py` routes `flow`→`pipeline`, `relations`→`hub-and-spoke`, `timeline`→`timeline`, `compare`→`before-after`, and `checklist`→`checklist` to `render_mode: "mermaid"`, `illustration_backend: "mermaid-render"`. Genuine numeric series (≥5 comparable values) stay on `html` + `data-first`; scenes/impacts stay on `ai` (Ian for technical tracks, clinical illustration for `medical`).
- `html_render.build_mermaid_source(spec)` maps the `structure` to a mermaid flowchart; `render_mermaid_png(spec, png)` renders it to SVG via the local `mermaid` package, wraps it in a WeChat-width HTML shell with a titled header, and screenshots it full-page with Playwright (auto height, no blank band).
- The renderer needs the `mermaid` npm package installed once at the skill root: `cd ~/.comate/skills/tom-wechat-publisher && npm install`. Playwright's Chromium (already required for HTML rendering) does the rasterization; no extra browser download.
- `rendered_by` must be `mermaid-render`. `validate_body_images.py` rejects a `mermaid` slot whose backend or `rendered_by` is not `mermaid-render`, and still requires `locked_facts` for any `high`-risk deterministic slot.

### Deterministic Swiss HTML layouts

The bundled HTML renderer includes a static subset adapted from the visual language of `guizang-ppt-skill`. This is an internal implementation, not a runtime dependency on that skill. It deliberately excludes WebGL, deck navigation, Motion animations, CDN fonts, and presentation-only chrome.

Use the registered layouts only when the content shape satisfies their semantic gate:

- `S03 Split Statement`: one central statement plus one explanation;
- `S05 Three Layers`: exactly three ordered architecture or protocol layers;
- `S07 Horizontal Bar`: 5-10 real comparable numeric values; never invent values to fill the chart;
- `S08 Duo Compare`: exactly two structurally comparable states or options;
- `S11 Horizontal Timeline`: 4-7 ordered steps;
- `S17 System Diagram`: exactly three nested `core -> middle -> outer` layers.

Route by explicit `structure` and item shape. A manually supplied `swiss_layout` may select a registered layout, but each renderer must still validate its item count and fall back to a general template when the semantic gate fails. Keep SVG geometric and put every visible label in deterministic HTML.

### Structured image specification

Each body image specification must include:

```json
{
  "render_mode": "ai | html | hybrid | mermaid",
  "fact_risk": "low | medium | high",
  "illustration_backend": "ian-xiaohei-illustrations | clinical-illustration | mermaid-render | html-render | create-image",
  "rendered_by": "actual backend recorded after generation",
  "overlay_applied": false,
  "structure": "pipeline | layer-stack | hub-and-spoke | before-after | data-first | text-scene | checklist",
  "purpose": "what reader confusion this image resolves",
  "labels": ["3-5 short labels when needed"],
  "locked_facts": ["exact strings, numbers, versions, code identifiers"],
  "qa_rules": ["slot-specific acceptance checks"]
}
```

Labels inside AI-generated pixels should normally be 2-5 Chinese characters. Put explanatory sentences in the article or deterministic overlay. English code identifiers belong in deterministic layers unless visual generation is specifically being repaired and OCR-verified.

### Enforced body-image gate

Body illustration planning is a required execution stage for technical articles, not an optional note. After the Markdown is finalized and before preview:

1. Run `image_spec.py` to create `image_specs.json`.
2. Keep 3-4 body slots by default; select cognitive anchors rather than mirroring every heading.
3. Put the opening image first in the Markdown, before the first level-two heading: technical tracks use the Ian Xiaohei `ai` + `low` scene; `medical` uses a clinical illustration or deterministic opener, never Ian. Render or generate the remaining factual images after it.
4. Render or generate the selected body images and insert their local Markdown references.
5. Run `validate_body_images.py` and reject missing specs, missing local files, stale filename mappings, misplaced images, a wrong-track first body image, or fewer than three body images. The article footer (see "Article footer") is exempt from spec mapping but must be the last image.
6. For `ai + low`, require `ian-xiaohei-illustrations` on technical tracks and `clinical-illustration` on `medical`, each with a matching `rendered_by` value before preview.
7. For `mermaid`, require `illustration_backend: mermaid-render` and `rendered_by: mermaid-render` before preview.
8. For `hybrid`, require `overlay_applied: true` before preview; a bare AI base is not a finished hybrid image.
9. Run OCR and visual QA for locked labels, then render the WeChat preview.

The preview command should include `--require-body-images` for technical articles. A successful cover-only preview must be reported as incomplete, never as a fully prepared illustrated article.

### Article-level mix

For a normal 3-4 image technical article:

- include at least one `ai` or visually substantial `hybrid` image (the first body slot is the Ian scene);
- include at least one deterministic diagram (`mermaid` or `html`) that carries exact facts;
- use at least two different visual structures;
- reject a set where every image is the same flow card, every image is HTML, or every image delegates exact facts to AI;
- reject any image with a large blank band; reroute sparse flows/comparisons/timelines to `mermaid` rather than the fixed 16:9 Swiss layouts;
- prefer the sequence `scene -> mechanism -> facts/conditions -> action/checklist`.

### Hybrid composition

For `hybrid` images:

1. Generate an unlabeled or minimally labeled AI base with stable object placement and safe margins.
2. Add exact labels, arrows, code identifiers, numbers, and captions with HTML/CSS or another deterministic renderer.
3. Keep overlays visually integrated. Avoid nested cards, dense legends, UI chrome, and floating labels unrelated to objects.
4. Use image-to-image repair for visual-object problems. Use deterministic overlays for factual text problems.

## Image Quality

Before upload, inspect all generated or converted images:

- Verify the cover is AI-generated and visibly follows the account's selected style: Chinese painting for technical tracks, clinical illustration for `medical`.
- Verify every body image matches its declared `render_mode`, `fact_risk`, `illustration_backend`, `rendered_by`, and `locked_facts`.
- For Ian slots, apply `ian-xiaohei-illustrations/references/qa-checklist.md`: pure white background, 小黑 as the core action, large blank space, sparse short labels, and no PPT/card-grid appearance.
- For `medical` clinical-illustration slots, verify: no cartoon or mascot, no identifiable patient or wristband, no gore or diagnostic image presented as fact, no readable drug name/dose/lab value, and a calm clinical editorial look.
- Reject garbled, duplicated, clipped, off-topic, or incorrectly connected labels.
- Confirm exact numbers, versions, code paths, category order, and configuration strings against the article.
- Confirm label positions point to the intended object or flow.
- Confirm the full subject and labels remain readable at WeChat article width.
- Reject accidental logos, watermarks, unrelated English, unsupported facts, and decorative clutter.
- Reject visually repetitive image sets or a set without both a track-appropriate visual anchor and a deterministic factual image.
- Regenerate, image-to-image repair, or reroute any image that fails its slot-specific QA rules.

Useful scripts:

```bash
python3 ~/.comate/skills/tom-wechat-publisher/scripts/ocr_validate.py IMAGE
python3 ~/.comate/skills/tom-wechat-publisher/scripts/compare_body_images.py ARTICLE_DIR
```

## 图片后端

AI 封面和 AI 正文图都走 `scripts/image_backend.py`。它的命令行和 `create-image` 的 `generate-image.sh` 一样（`--prompt`、`--output <文件名>`、`--input`、`--resolution 1K|2K|4K`、`--aspect-ratio`），图片写到 `./images/<output>`，退出码沿用 0/2/3/4/5/10/11/12/13。`generate_article.py` 和 `compare_body_images.py` 都通过它出图，生成前用 `--check` 判断后端能不能用。

选择顺序是环境变量 `TOM_WECHAT_IMAGE_BACKEND`，其次账号 profile 的 `image_backend` 字段（`--account` 或 `TOM_WECHAT_ACCOUNT` 指定的账号），都没有时用 `comate`。

| 后端 | 用途 | 需要的设置 |
| --- | --- | --- |
| `comate` | 默认。原样调用内网 `create-image` 的 `generate-image.sh` | Comate 登录态或 `COMATE_USERNAME_ENCRYPTED`；`TOM_WECHAT_CREATE_IMAGE_BASH` 仍可覆盖脚本路径 |
| `openai` | OpenAI 兼容的 `/images/generations`，带 `--input` 时走 `/images/edits` | `TOM_WECHAT_IMAGE_MODEL`；`TOM_WECHAT_IMAGE_BASE_URL` 默认 `https://api.openai.com/v1`；`TOM_WECHAT_IMAGE_SIZES` 列出模型接受的尺寸，默认 `1024x1024,1536x1024,1024x1536` |
| `gemini` | Google `models/<model>:generateContent`，读取返回的内联图片（Nano Banana 一类 `gemini-*-image` 模型） | `TOM_WECHAT_IMAGE_MODEL`；`TOM_WECHAT_IMAGE_BASE_URL` 默认 `https://generativelanguage.googleapis.com/v1beta` |
| `command` | 任意其他工具 | `TOM_WECHAT_IMAGE_COMMAND`，argv 模板，占位符 `{prompt} {output} {input} {aspect} {resolution}`，不经过 shell，`{output}` 是必须写入的绝对路径 |

`openai` 和 `gemini` 的密钥先读 `TOM_WECHAT_IMAGE_API_KEY`，再读 macOS 钥匙串，服务名 `tom-wechat-publisher-image`，账户名是 base URL 的主机名：

```bash
security add-generic-password -U -s tom-wechat-publisher-image -a generativelanguage.googleapis.com -w
python3 ~/.comate/skills/tom-wechat-publisher/scripts/image_backend.py --check
```

`--check` 打印选中的后端、来源、缺什么，不打印密钥。宽高比会同时写进提示词，因为不少模型会忽略尺寸参数。超时和重试沿用 `TOM_WECHAT_CREATE_IMAGE_TIMEOUT`、`TOM_WECHAT_CREATE_IMAGE_ATTEMPTS`，只重试网络错误、429 和 5xx。后端不可用时仍按 `TOM_WECHAT_AI_FALLBACK_HTML` 决定是退回 HTML 还是报错，不会自动换到另一个 AI 后端。

## Failure Diagnosis

Classify failures before retrying:

- **Preflight**: missing source, cover, image, Python, adapter, or credentials.
- **Preparation**: unsupported image conversion or malformed metadata.
- **Prose revision**: `check_prose.py` reports banned punctuation, reversal templates, jargon, or signposts.
- **Rendering**: Markdown/theme conversion or preview generation failure.
- **Media upload**: cover/body image API failure, including errors such as `40113`.
- **Draft creation**: API rejection after media upload.

For `40113`, inspect the exact failing asset, its final encoded format, dimensions, extension, and the adapter/API response. Do not blindly rerun the entire workflow.

## Completion Report

After preparation, report:

- Prepared Markdown path.
- Preview path or paths.
- Resolved title, author, cover, body image count, theme, account profile, and content track.
- The `check_prose.py` result for the delivered text.
- Any warnings or skipped optional checks.

After upload, additionally report:

- Whether a draft was created.
- The target AppID in masked form only (for example, `test...p-id`); never print the full AppID or App Secret.
- The adapter-provided draft Media ID only when the upload output contains explicit success wording and a parseable Media ID. An exit code alone is not evidence.
- The precise failed stage and response when unsuccessful.

Never describe a preview or dry run as a successful draft upload.

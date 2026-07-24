---
name: tom-wechat-publisher
description: Generate, polish, illustrate, prepare, validate, preview, and explicitly upload WeChat Official Account draft articles. Trigger whenever the user asks to turn a URL, web announcement, local file, meeting notes, conversation, summary, or topic brief into a WeChat article, or mentions 公众号, 微信公众号, 草稿箱, Markdown/md 公众号排版, 草稿上传, 封面处理, 正文图片兼容, 公众号预览, or WeChat media errors such as 40113. It supports source-grounded article generation, direct conversation/context-to-article workflows, local Markdown preparation, AI Chinese-painting covers, deterministic factual infographics, and draft-only upload. Do not trigger for standalone illustration generation, GitHub Pages publishing, or mass sending. This skill may create drafts only after explicit user authorization; it must never publish or mass-send.
---

# WeChat Draft Publisher

## Purpose

Use this skill to turn a URL, local source file, conversation, meeting note, summary, or topic brief into a grounded WeChat article, or to prepare an existing Markdown article. The workflow can generate and inspect the article's cover and body illustrations, render the exact themed output with the installed Wenyan CLI, and optionally create a WeChat draft through Wenyan's draft-only publishing path.

The default workflow stops after preparation and preview. Creating a draft is a side effect and requires explicit user authorization in the current request.

Use `赵涛` as the default article author. Resolve author precedence as `--author` > `WECHAT_AUTHOR` > `赵涛`, so explicit per-article and environment overrides continue to work.

## Scope

This skill can:

1. Generate a grounded article from a URL, local text/Markdown source, conversation context, meeting notes, summary, or topic brief.
2. Enrich source material with optional web search and preserve uncertainty instead of inventing facts.
3. Generate and route the cover and body illustrations according to factual risk.
4. Normalize Markdown metadata and local image references.
5. Convert unsupported local images into WeChat-compatible formats.
6. Validate the article, cover, and body images before upload.
7. Render one or more local HTML previews with bundled themes.
8. Create a WeChat draft through an explicitly configured upload command.
9. Diagnose failures by stage: source ingestion, fact extraction, image generation, preflight, preparation, rendering, media upload, or draft creation.

This skill must not:

- Publish or mass-send a WeChat article.
- Upload when the user only asked to write, polish, convert, inspect, or preview.
- Claim upload success from an exit code alone when the adapter output reports failure.
- Generate article illustrations itself when a dedicated illustration skill is a better fit.
- Handle GitHub Pages publishing; use `tom-pages-publisher` for that.

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
and all referenced local images outside the temporary staging directory.

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

## Preflight Rules

The wrapper must verify:

- Python 3.9 or newer is available. `TOM_WECHAT_PYTHON` may select a specific executable.
- The source Markdown exists and is readable.
- Local Markdown and HTML image references resolve.
- The cover exists locally and the mandatory AI cover metadata contract is valid.
- Prepared images are JPEG, PNG, or GIF.
- High-risk HTML/hybrid specs contain `locked_facts`, and body labels are article-specific.
- Wenyan CLI is installed and executable for preview and upload.
- Upload is not attempted with `--dry-run`, `--render-only`, or theme-preview mode.

Use `--keep-temp` while diagnosing problems. Temporary output otherwise belongs under the system temporary directory and is removed on exit.
Failure notifications are disabled by default. Set `TOM_WECHAT_FAILURE_NOTIFY` to an explicit 如流 recipient to opt in.

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

Do not invent facts while polishing. Preserve source facts, causal wording, uncertainty, dates, product names, and quoted claims.

## Mandatory AI Chinese-Painting Cover

Every article prepared by this skill must use an AI-generated cover in a Chinese painting style. This is a hard requirement, not a visual suggestion.

Before generating the cover:

1. Select exactly one style for the current article. Ask the user when they are choosing the visual direction; otherwise choose the best match and report it.
2. Allowed styles are: `水墨写意`, `工笔重彩`, `青绿山水`, `宋画风`, `敦煌壁画风`, `文人画`, and `新中式国风`.
3. Generate the cover through the `create-image` skill or its bundled image-generation wrapper. The cover must be genuinely AI-generated.
4. Use a 2.35:1 composition suitable for a WeChat headline cover. Generate at 16:9 when the image API does not support 2.35:1, then crop deliberately to 1800x766 while preserving the focal subject.
5. Store provenance in frontmatter:

```yaml
cover: "images/cover-ai.png"
cover_generation: "ai"
cover_style: "青绿山水"
```

Do not use HTML/CSS rendering, charts, screenshots, stock images, old article assets, or deterministic diagrams as the article cover. HTML-rendered images are allowed only for label-heavy body infographics.

The cover should express the article concept through Chinese visual language rather than imitate a generic cybersecurity poster.

### Mandatory poetic inscription

Every cover must include an original poetic inscription derived from the article. Use exactly two or four Chinese poetic lines.

1. Write the poem after the article is finalized. Extract the article's central image, tension, and consequence rather than paraphrasing its title.
2. Keep the language concise and classically inflected, but understandable. Do not force technical terms, product names, English, CVE/GHSA identifiers, or marketing slogans into the poem.
3. Check that the poem does not invent factual claims. It is a visual metaphor, not evidence or a technical summary.
4. Generate the poem first, then generate the Chinese painting, calligraphy, seal, negative space, and focal subject together in one AI composition. The inscription must feel painted into the same visual world, like a traditional title inscription, not pasted on afterward.
5. Prefer integrated horizontal or vertical calligraphy placed directly in natural mist, sky, water, paper texture, or other calm negative space. Match ink density, brush texture, scale, rhythm, and seal placement to the painting.
6. Explicitly forbid independent text panels, floating cards, opaque banners, rectangular paper strips, UI-like containers, and visibly composited backing blocks. Do not reproduce the separated 'poem pasted over painting' look.
7. After the first generation, verify every character and the overall integration. The rendered poem must exactly match the approved lines and contain no quotation marks, book-title marks, brackets, commas, periods, decorative punctuation, or extra characters unless the user explicitly approved them. Treat surrounding `“”`, `‘’`, `「」`, `『』`, `《》`, or sentence-ending punctuation as errors. If the poem has a wrong character, extra punctuation, missing line, bad spacing, or weak visual unity, use image-to-image editing with the generated cover as input and request a localized correction while preserving the painting.
8. Repeat image-to-image repair when useful. Use deterministic text compositing only as the final fallback when image-to-image cannot produce correct text. Even in fallback mode, text must sit directly in existing negative space with transparent treatment; never add a separate panel or banner.
9. Preserve mobile legibility without making the poem dominate the painting. The title inscription, landscape rhythm, and red seal should form one balanced composition.

Record the final inscription and composition in frontmatter:

```yaml
cover: "images/cover-ai-poem.jpg"
cover_generation: "ai"
cover_style: "青绿山水"
cover_poem:
  - "第一句"
  - "第二句"
cover_typography: "vertical"
```

`cover_poem` must contain exactly 2 or 4 non-empty lines. `cover_typography` must be one of `vertical`, `horizontal`, or `split`.

HTML/CSS text compositing is a last-resort fallback only after at least one integrated generation and one image-to-image repair attempt fail. It must never replace the AI painting and must never introduce a visible panel, banner, card, or backing rectangle.

Before preview or upload, stop with an error if the cover is missing, `cover_generation` is not `ai`, `cover_style` is outside the allowed list, the poem does not contain 2 or 4 lines, or typography is invalid. Do not silently accept a technically valid but non-compliant cover.

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

For technical articles, make the first body image an editorial visual anchor: place one `ai` + `low` Ian Xiaohei illustration before the first `##` heading. 小黑 must perform the article's central action on a pure-white hand-drawn scene. Put exact versions, CVE identifiers, commands, tables, and checklists in later deterministic HTML or hybrid images. `validate_body_images.py` rejects any article whose first body image is not an Ian Xiaohei `ai` + `low` slot.

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

Assign every image one `render_mode` and one `fact_risk`:

- `ai` + `low`: delegate the complete body illustration to `ian-xiaohei-illustrations`. Use Ian's pure-white, minimalist black hand-drawn visual DNA; 小黑 must perform the core conceptual action. Use no text or at most 3-5 short Chinese labels, and run Ian's QA checklist. Ian invokes `create-image` as its image backend.
- `html` + `high`: versions, numbers, commands, configuration, code paths, long labels, tables, timelines, and checklists. Render all facts deterministically.
- `hybrid` + `medium/high`: mechanism, architecture, or attack path that needs visual material plus exact labels. Keep the current deterministic HTML/hybrid route; do not delegate locked facts to Ian or any free-form image prompt.

For low-risk AI body slots, `tom-wechat-publisher` owns article selection and fact-risk classification, while `ian-xiaohei-illustrations` owns the shot metaphor, 小黑 action, prompt construction, `create-image` invocation, and Ian QA. The cover remains on the mandatory Chinese-painting workflow. The image model must not infer or rewrite exact data.

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
  "render_mode": "ai | html | hybrid",
  "fact_risk": "low | medium | high",
  "illustration_backend": "ian-xiaohei-illustrations | html-render | create-image",
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
3. Put the Ian Xiaohei `ai` + `low` scene first in the Markdown, before the first level-two heading; render or generate the remaining factual images after it.
4. Render or generate the selected body images and insert their local Markdown references.
5. Run `validate_body_images.py` and reject missing specs, missing local files, stale filename mappings, misplaced images, a non-Ian first body image, or fewer than three body images.
6. For `ai + low`, require `illustration_backend: ian-xiaohei-illustrations` and a matching `rendered_by` value before preview.
7. For `hybrid`, require `overlay_applied: true` before preview; a bare AI base is not a finished hybrid image.
8. Run OCR and visual QA for locked labels, then render the WeChat preview.

The preview command should include `--require-body-images` for technical articles. A successful cover-only preview must be reported as incomplete, never as a fully prepared illustrated article.

### Article-level mix

For a normal 3-4 image technical article:

- include at least one `ai` or visually substantial `hybrid` image;
- include at least one `html` image for exact facts;
- use at least two different visual structures;
- reject a set where every image is a flow card, every image is HTML, or every image delegates exact facts to AI;
- prefer the sequence `scene -> mechanism -> facts/conditions -> action/checklist`.

### Hybrid composition

For `hybrid` images:

1. Generate an unlabeled or minimally labeled AI base with stable object placement and safe margins.
2. Add exact labels, arrows, code identifiers, numbers, and captions with HTML/CSS or another deterministic renderer.
3. Keep overlays visually integrated. Avoid nested cards, dense legends, UI chrome, and floating labels unrelated to objects.
4. Use image-to-image repair for visual-object problems. Use deterministic overlays for factual text problems.

## Image Quality

Before upload, inspect all generated or converted images:

- Verify the cover is AI-generated and visibly follows the selected Chinese painting style.
- Verify every body image matches its declared `render_mode`, `fact_risk`, `illustration_backend`, `rendered_by`, and `locked_facts`.
- For Ian slots, apply `ian-xiaohei-illustrations/references/qa-checklist.md`: pure white background, 小黑 as the core action, large blank space, sparse short labels, and no PPT/card-grid appearance.
- Reject garbled, duplicated, clipped, off-topic, or incorrectly connected labels.
- Confirm exact numbers, versions, code paths, category order, and configuration strings against the article.
- Confirm label positions point to the intended object or flow.
- Confirm the full subject and labels remain readable at WeChat article width.
- Reject accidental logos, watermarks, unrelated English, unsupported facts, and decorative clutter.
- Reject visually repetitive image sets or a set without both an AI visual anchor and a deterministic factual image.
- Regenerate, image-to-image repair, or reroute any image that fails its slot-specific QA rules.

Useful scripts:

```bash
python3 ~/.comate/skills/tom-wechat-publisher/scripts/ocr_validate.py IMAGE
python3 ~/.comate/skills/tom-wechat-publisher/scripts/compare_body_images.py ARTICLE_DIR
```

## Failure Diagnosis

Classify failures before retrying:

- **Preflight**: missing source, cover, image, Python, adapter, or credentials.
- **Preparation**: unsupported image conversion or malformed metadata.
- **Rendering**: Markdown/theme conversion or preview generation failure.
- **Media upload**: cover/body image API failure, including errors such as `40113`.
- **Draft creation**: API rejection after media upload.

For `40113`, inspect the exact failing asset, its final encoded format, dimensions, extension, and the adapter/API response. Do not blindly rerun the entire workflow.

## Completion Report

After preparation, report:

- Prepared Markdown path.
- Preview path or paths.
- Resolved title, author, cover, body image count, and theme.
- Any warnings or skipped optional checks.

After upload, additionally report:

- Whether a draft was created.
- The adapter-provided draft/media identifier when available.
- The precise failed stage and response when unsuccessful.

Never describe a preview or dry run as a successful draft upload.

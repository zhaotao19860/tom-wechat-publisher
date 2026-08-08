# Cover Quatrain and Publish Safety Design

## Context

The cover contract currently accepts either two or four poem lines. The image specification generator and image prompt also prefer two lines in several fallback paths. As a result, newly generated covers can receive a two-line inscription even when a four-line quatrain is desired.

The latest draft upload also exposed four gaps between the documented contract and the executable workflow:

- the wrapper prints `Draft created successfully` after any zero exit status without requiring an explicit media identifier;
- the persistent preview can preserve Wenyan HTML without a UTF-8 charset declaration;
- the documented human-prose gate is not invoked by `publish_draft.sh`;
- the wrapper does not show even a masked account identifier before upload.

## Goals

1. Generate exactly four poem lines for every newly created or regenerated cover.
2. Improve the default poem shape so the four lines form one coherent quatrain rather than four unrelated keywords.
3. Keep existing articles with two-line `cover_poem` metadata valid and unchanged.
4. Require explicit draft evidence before reporting upload success.
5. Make the selected WeChat account auditable without exposing credentials.
6. Preserve UTF-8 rendering in persistent previews.
7. Enforce the documented prose gate in the preview and upload wrapper.

## Non-Goals

- Do not rewrite historical article Markdown or cover images.
- Do not reject existing two-line `cover_poem` metadata.
- Do not add a cover-contract version field.
- Do not publish or mass-send an article.
- Do not embed a personal final-image path in this reusable skill.

## Cover Poem Contract

New poem generation returns exactly four non-empty Chinese lines. A new quatrain uses one consistent line length of five or seven Chinese characters and follows this semantic shape:

1. establish the article's central scene;
2. introduce its tension or movement;
3. show the relevant human action or decision;
4. leave the concrete consequence or unresolved question.

The lines contain no punctuation, quotation marks, technical identifiers, product names, English, or title paraphrases. They remain metaphorical and must not introduce factual claims.

`image_spec._cover_poem_lines()` will always return four lines, including topic-specific and generic fallback paths. The cover image prompt will request exactly those four lines and will no longer permit dropping lines when rendering becomes difficult. OCR and image-to-image repair remain responsible for resolving character errors.

The preparation and asset validators continue to accept either two or four lines. Two-line acceptance exists only for historical compatibility. `SKILL.md` and `README.md` will state that agents must never choose two lines for a new or regenerated cover.

## Publish Safety

Before invoking `wenyan publish`, the wrapper prints a masked AppID that exposes only a small prefix and suffix. It never prints the secret.

After Wenyan exits successfully, the wrapper must find both explicit success wording and a non-empty Media ID in the captured output. An exit status of zero without this evidence is a draft-creation failure. The wrapper reports the parsed identifier in a normalized line and only then prints `Draft created successfully`.

Existing error-pattern checks remain in place and take precedence over success detection.

## Prose Gate

`publish_draft.sh` resolves `check_prose.py` through an explicit `TOM_WECHAT_CHECK_PROSE` override first, followed by supported local skill locations. Preview and upload stop if the checker is missing or returns non-zero.

The gate runs against the source article after body-image validation and before staging or rendering. Its output is preserved so the operator can see the checked character count and any blocked patterns.

## Persistent Preview

`persist_preview.py` reads the rendered HTML as UTF-8 and ensures the persisted file contains `<meta charset="utf-8">`. If the declaration is absent, it is inserted at the start of `<head>`; if no `<head>` exists, a minimal head element is added after the opening `<html>` tag or before the document body as a final fallback.

Asset-copy behavior remains unchanged.

## Tests

Tests are written and observed failing before implementation.

- topic-specific and generic generated poems contain exactly four lines;
- generated lines use one five- or seven-character length where the deterministic generator controls the text;
- an unchanged historical two-line article still passes both preparation and asset validation;
- the cover prompt requires exactly four lines and does not permit fewer lines;
- persistent preview output contains a UTF-8 charset declaration when the source preview omitted it;
- zero-exit Wenyan output without a Media ID fails the upload;
- explicit Wenyan success with a Media ID succeeds and reports the identifier;
- upload output includes a masked AppID but never the AppSecret;
- preview preparation invokes the prose checker and propagates a checker failure;
- the complete existing unit-test suite remains green.

## Error Handling

Each new failure identifies its stage:

- `prose preflight` for missing or failed prose checks;
- `persistent preview` for unreadable or unwritable preview files;
- `draft creation` for missing explicit success evidence or Media ID;
- existing Wenyan and API errors retain their original output.

No retry is added for deterministic validation failures. Image repair remains part of the existing cover-generation loop.

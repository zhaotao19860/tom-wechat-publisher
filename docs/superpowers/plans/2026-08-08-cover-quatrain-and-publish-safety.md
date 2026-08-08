# Cover Quatrain and Publish Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every newly generated cover use a coherent four-line Chinese quatrain while preserving legacy two-line articles and tightening preview and draft-upload verification.

**Architecture:** Keep historical compatibility in the existing metadata validators, and enforce the new default at the poem-generation and image-prompt boundaries. Add focused Python helpers for persistent-preview charset normalization and Wenyan upload-evidence parsing; keep Bash responsible for prose preflight, masked account reporting, rendering, and upload orchestration.

**Tech Stack:** Python 3.9+, Bash 3.2-compatible shell, `unittest`, Wenyan CLI 2.x, Markdown/YAML frontmatter.

## Global Constraints

- New or regenerated covers contain exactly four non-empty Chinese poem lines.
- New quatrains use one consistent five- or seven-character line length.
- Existing two-line `cover_poem` metadata remains valid and unchanged.
- `--upload` creates drafts only; never publish or mass-send.
- Upload success requires explicit success wording and a non-empty Media ID.
- App secrets never appear in output, logs, Markdown, or tests.
- Persistent previews declare UTF-8.
- Preview and upload stop when the prose checker is missing or fails.
- Do not embed personal asset paths in this reusable skill.

---

### Task 1: Four-Line Cover Poem Generation

**Files:**
- Modify: `tests/test_article_assets.py`
- Modify: `scripts/image_spec.py:707`
- Modify: `scripts/generate_article.py:1982`

**Interfaces:**
- Consumes: `image_spec._cover_poem_lines(title: str, sections: list[tuple[str, str]]) -> list[str]`
- Produces: a four-element poem list and a cover prompt that requires every supplied line verbatim.

- [ ] **Step 1: Write failing generation tests**

Add to `tests/test_article_assets.py`:

```python
def assert_generated_quatrain(test: unittest.TestCase, lines: list[str]) -> None:
    test.assertEqual(len(lines), 4)
    test.assertEqual(len({len(line) for line in lines}), 1)
    test.assertIn(len(lines[0]), {5, 7})
    for line in lines:
        test.assertRegex(line, r"^[\u4e00-\u9fff]{5,7}$")


def test_generated_cover_poems_are_four_line_quatrains(self) -> None:
    image_spec = load_script("image_spec.py")
    cases = [
        ("DMARC 与 DNSSEC", [("原理", "Compact Denial 与 NXNAME 处理空名")]),
        ("DNS 免费服务", [("收费", "基础设施成本与 SLA 付费")]),
        ("普通技术文章", [("现象", "团队在夜间核对服务记录")]),
    ]
    for title, sections in cases:
        with self.subTest(title=title):
            assert_generated_quatrain(self, image_spec._cover_poem_lines(title, sections))
```

Add a prompt test using `generate_article._build_image_prompt()` with four known lines. Assert every line appears, `exactly four lines` appears, and `prefer fewer` and `two lines` do not appear.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_article_assets.ArticleAssetTests.test_generated_cover_poems_are_four_line_quatrains tests.test_article_assets.ArticleAssetTests.test_cover_prompt_requires_all_four_poem_lines -v
```

Expected: FAIL because topic-specific and fallback paths still return two lines and the prompt permits fewer lines.

- [ ] **Step 3: Implement the four-line generator**

In `scripts/image_spec.py`, add a deterministic filler and change every branch to return four lines:

```python
DEFAULT_COVER_QUATRAIN = ["山水开新卷", "风云入远汀", "孤灯查旧录", "一问见分明"]


def _four_line_cover_poem(candidates: list[str]) -> list[str]:
    lines = [line for line in candidates if re.fullmatch(r"[\u4e00-\u9fff]{5}", line)]
    for fallback in DEFAULT_COVER_QUATRAIN:
        if fallback not in lines:
            lines.append(fallback)
        if len(lines) == 4:
            break
    return lines[:4]
```

Use article-specific five-character phrases before fallback lines. Change `_cover_poem_lines` documentation from “two or four” to “four”.

In `scripts/generate_article.py`, use this fallback instruction:

```python
poem_instruction = cover_poem_block or "derive an exact four-line Chinese quatrain from the article title and section notes"
```

Require exactly four lines. Replace the fewer-line escape clause with an instruction to repair unclear characters without omitting, merging, duplicating, or replacing a line.

- [ ] **Step 4: Verify GREEN and legacy compatibility**

Run:

```bash
python3 -m unittest tests.test_article_assets -v
```

Expected: all tests PASS, including the existing two-line frontmatter fixtures.

- [ ] **Step 5: Commit**

```bash
git add tests/test_article_assets.py scripts/image_spec.py scripts/generate_article.py
git commit -m "feat: generate four-line cover quatrains"
```

---

### Task 2: UTF-8 Persistent Preview

**Files:**
- Modify: `tests/test_compare_and_preview.py`
- Modify: `scripts/persist_preview.py`

**Interfaces:**
- Produces: `ensure_utf8_charset(html: str) -> str`, called before writing persistent `preview.html`.

- [ ] **Step 1: Write failing charset tests**

Add a test that persists `<html><head><title>预览</title></head><body>中文</body></html>` and asserts the output contains `<meta charset="utf-8">`. Add a direct test for a fragment without `<head>`:

```python
def test_charset_normalizer_handles_fragment_without_head(self) -> None:
    persist = load_script("persist_preview.py")
    normalized = persist.ensure_utf8_charset("<section>中文</section>")
    self.assertTrue(normalized.startswith('<meta charset="utf-8">'))
```

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_compare_and_preview.CompareAndPreviewTests.test_persistent_preview_adds_utf8_charset tests.test_compare_and_preview.CompareAndPreviewTests.test_charset_normalizer_handles_fragment_without_head -v
```

Expected: FAIL because the current code copies HTML unchanged and has no normalizer.

- [ ] **Step 3: Implement charset normalization**

Add to `scripts/persist_preview.py`:

```python
CHARSET_RE = re.compile(r"<meta\s+[^>]*charset\s*=", flags=re.I)


def ensure_utf8_charset(html: str) -> str:
    if CHARSET_RE.search(html):
        return html
    meta = '<meta charset="utf-8">'
    if re.search(r"<head(?:\s[^>]*)?>", html, flags=re.I):
        return re.sub(r"(<head(?:\s[^>]*)?>)", rf"\1{meta}", html, count=1, flags=re.I)
    if re.search(r"<html(?:\s[^>]*)?>", html, flags=re.I):
        return re.sub(r"(<html(?:\s[^>]*)?>)", rf"\1<head>{meta}</head>", html, count=1, flags=re.I)
    return meta + html
```

Write normalized HTML instead of calling `shutil.copy2` for the HTML file. Leave referenced-asset copying unchanged.

- [ ] **Step 4: Verify GREEN**

```bash
python3 -m unittest tests.test_compare_and_preview -v
```

Expected: all preview tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_compare_and_preview.py scripts/persist_preview.py
git commit -m "fix: preserve UTF-8 in persistent previews"
```

---

### Task 3: Enforced Human-Prose Preflight

**Files:**
- Modify: `tests/test_publish_safety.py`
- Modify: `scripts/publish_draft.sh:27`
- Modify: `scripts/publish_draft.sh:450`

**Interfaces:**
- Consumes: `TOM_WECHAT_CHECK_PROSE`, followed by supported `human-writing/scripts/check_prose.py` locations.
- Produces: `resolve_check_prose()` path output; wrapper invokes `python CHECKER ARTICLE` before staging.

- [ ] **Step 1: Write failing integration tests**

Refactor the existing fake-wrapper setup into a helper that creates valid article, fake Wenyan, and fake prose checker fixtures. Set `TOM_WECHAT_CHECK_PROSE` in every wrapper test. Add one test that asserts a checker marker exists and one that configures exit 7 and asserts render was never called.

```python
def test_preview_runs_configured_prose_checker(self) -> None:
    result, fixture = self.run_wrapper_with_fakes(upload=False, checker_exit=0)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertTrue(fixture.checker_marker.is_file())


def test_prose_checker_failure_stops_before_render(self) -> None:
    result, fixture = self.run_wrapper_with_fakes(upload=False, checker_exit=7)
    self.assertEqual(result.returncode, 7)
    self.assertNotIn("render ", fixture.wenyan_log.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_publish_safety.PublishSafetyTests.test_preview_runs_configured_prose_checker tests.test_publish_safety.PublishSafetyTests.test_prose_checker_failure_stops_before_render -v
```

Expected: FAIL because the wrapper never invokes the configured checker.

- [ ] **Step 3: Implement checker resolution and invocation**

Document `TOM_WECHAT_CHECK_PROSE` in usage. Add a resolver that checks the explicit override, then `~/.codex`, `~/.agents`, and `~/.comate` human-writing skill locations. After body-image validation and before `mktemp`, run:

```bash
if ! check_prose_script="$(resolve_check_prose)"; then
  echo "ERROR: prose preflight checker not found; set TOM_WECHAT_CHECK_PROSE." >&2
  exit 2
fi
wechat_failure_context="prose preflight: ${article}"
"$PYTHON" "$check_prose_script" "$article" >&2
```

Preserve checker output and exit status.

- [ ] **Step 4: Verify GREEN**

```bash
python3 -m unittest tests.test_publish_safety -v
```

Expected: all publish safety tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_safety.py scripts/publish_draft.sh
git commit -m "feat: enforce prose checks before WeChat rendering"
```

---

### Task 4: Explicit Draft Evidence and Masked Account Identity

**Files:**
- Create: `scripts/validate_publish_output.py`
- Create: `tests/test_publish_output.py`
- Modify: `tests/test_publish_safety.py`
- Modify: `scripts/publish_draft.sh:603`

**Interfaces:**
- Produces: `extract_draft_media_id(output: str) -> str`; raises `ValueError` unless success wording and a Media ID both exist.
- Consumes: Wenyan's captured `upload.log` after existing exit-status and error-pattern checks.

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_publish_output.py` with three cases:

```python
def test_extracts_media_id_from_wenyan_success(self) -> None:
    output = "发布成功，Media ID: WbRk3xhNjj6cpC0OM8LkSyAVTdT_RnFK\n"
    self.assertEqual(validator.extract_draft_media_id(output), "WbRk3xhNjj6cpC0OM8LkSyAVTdT_RnFK")

def test_rejects_output_without_media_id(self) -> None:
    with self.assertRaisesRegex(ValueError, "Media ID"):
        validator.extract_draft_media_id("publish completed\n")

def test_rejects_media_id_without_success_wording(self) -> None:
    with self.assertRaisesRegex(ValueError, "success"):
        validator.extract_draft_media_id("media_id=orphan-id\n")
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_publish_output -v
```

Expected: ERROR because `scripts/validate_publish_output.py` does not exist.

- [ ] **Step 3: Implement the parser**

Create `scripts/validate_publish_output.py` with:

```python
SUCCESS_RE = re.compile(r"发布成功|draft\s+created\s+successfully|publish(?:ed)?\s+successfully", re.I)
MEDIA_ID_RE = re.compile(r"(?:media[ _-]?id)\s*[:=]\s*([A-Za-z0-9_-]+)", re.I)

def extract_draft_media_id(output: str) -> str:
    if not SUCCESS_RE.search(output):
        raise ValueError("explicit draft success wording was not found")
    match = MEDIA_ID_RE.search(output)
    if not match:
        raise ValueError("draft Media ID was not found")
    return match.group(1)
```

Its CLI accepts one upload-log path, prints only the Media ID on success, and returns non-zero with `draft creation evidence invalid` on read or validation failure.

- [ ] **Step 4: Verify parser GREEN**

```bash
python3 -m unittest tests.test_publish_output -v
```

Expected: all parser tests PASS.

- [ ] **Step 5: Write failing wrapper tests**

Make fake Wenyan output configurable. Add a success case that expects masked `test...p-id`, normalized `Draft Media ID: test-draft`, and no secret. Add a zero-exit `completed` case that expects non-zero status and no `Draft created successfully`.

- [ ] **Step 6: Verify wrapper RED**

```bash
python3 -m unittest tests.test_publish_safety.PublishSafetyTests.test_upload_reports_masked_app_id_and_media_id tests.test_publish_safety.PublishSafetyTests.test_upload_rejects_zero_exit_without_explicit_draft_evidence -v
```

Expected: FAIL because the wrapper neither masks the AppID nor validates upload evidence.

- [ ] **Step 7: Integrate masking and evidence validation**

Add a Bash `mask_app_id()` helper that returns `***` for values of eight characters or fewer, otherwise the first four characters, `...`, and last four characters. Print `Target WeChat AppID` after credentials resolve. After existing error checks, run:

```bash
wechat_failure_context="draft creation evidence: ${article}"
if ! draft_media_id="$("$PYTHON" "$script_dir/validate_publish_output.py" "$upload_log")"; then
  echo "ERROR: draft creation evidence validation failed." >&2
  exit 1
fi
printf 'Draft Media ID: %s\n' "$draft_media_id"
printf 'Draft created successfully.\n'
```

- [ ] **Step 8: Verify wrapper GREEN**

```bash
python3 -m unittest tests.test_publish_safety -v
```

Expected: all publish safety tests PASS.

- [ ] **Step 9: Commit**

```bash
git add scripts/validate_publish_output.py tests/test_publish_output.py tests/test_publish_safety.py scripts/publish_draft.sh
git commit -m "fix: require explicit WeChat draft evidence"
```

---

### Task 5: Documentation and Full Verification

**Files:**
- Modify: `SKILL.md:235`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: documentation that distinguishes new four-line generation from legacy two-line compatibility.

- [ ] **Step 1: Update contracts and examples**

In `SKILL.md` and `README.md`, require exactly four lines for new or regenerated covers, define the scene/tension/action/consequence shape, use four lines in examples, and state that two-line validation exists only for unchanged historical articles. Document `TOM_WECHAT_CHECK_PROSE`, masked AppID output, explicit Media ID evidence, and UTF-8 persistence. Add a dated `CHANGELOG.md` entry.

- [ ] **Step 2: Scan for contradictory generation guidance**

```bash
rg -n "two-line|two lines|two or four|2 or 4|二句或四句|两句或四句|prefer fewer|derive a two-line" SKILL.md README.md scripts tests
```

Expected: any remaining two-line reference is explicitly labeled historical compatibility; no generation prompt permits fewer than four lines.

- [ ] **Step 3: Run all tests**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: zero failures and zero errors.

- [ ] **Step 4: Run syntax and whitespace checks**

```bash
python3 -m py_compile scripts/*.py tests/*.py
bash -n scripts/publish_draft.sh
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 5: Re-run the high-risk integration modules**

```bash
python3 -m unittest tests.test_article_assets tests.test_compare_and_preview tests.test_publish_safety tests.test_publish_output -v
```

Expected: legacy two-line preparation passes, generated cover specs contain four lines, preview output declares UTF-8, prose preflight runs, and fake upload requires explicit Media ID evidence.

- [ ] **Step 6: Commit documentation**

```bash
git add SKILL.md README.md CHANGELOG.md
git commit -m "docs: require four-line poems for new covers"
```

- [ ] **Step 7: Review final repository state**

```bash
git log -6 --oneline
git status --short
```

Expected: the planned commits are present and the worktree is clean.

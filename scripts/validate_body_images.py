#!/usr/bin/env python3
"""Fail preflight when body-image specs, files, and article placement diverge."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


GENERIC_LABELS = {"结果是什么", "要理解这个bug", "先说影响面"}
# The article footer is not a body illustration: it has no spec and must be the last image.
# Accounts may override the footer via TOM_WECHAT_FOOTER_IMAGE; the default keeps
# the historical guofeng seal.
DEFAULT_FOOTER = "footer-guofeng-seal"
# Footers replaced on 2026-10-08; still referencing one means the article was not updated.
LEGACY_FOOTER_STEMS = {"wechat", "wechat-sign"}


def _article_track(text: str) -> str:
    match = re.search(r"(?mi)^track\s*:\s*[\"']?([^\"'\n#]+)", text)
    if not match:
        return "dns"
    return match.group(1).strip().lower() or "dns"


def _normalize_label(value: object) -> str:
    return re.sub(r"[\s，,。！？!?：:；;]+", "", str(value or "")).lower()


def _section_for_offset(text: str, offset: int) -> str:
    headings = list(re.finditer(r"^##\s+(.+?)\s*$", text[:offset], flags=re.MULTILINE))
    return headings[-1].group(1).strip() if headings else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("article", type=Path)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--min", type=int, default=3)
    parser.add_argument("--footer", default=os.environ.get("TOM_WECHAT_FOOTER_IMAGE", DEFAULT_FOOTER))
    args = parser.parse_args()

    footer_stem = Path(str(args.footer or DEFAULT_FOOTER)).stem or DEFAULT_FOOTER
    footer_stems = {footer_stem}
    specs = json.loads(args.spec.read_text(encoding="utf-8"))
    # Older articles listed the footer in image_specs.json to get past this check; ignore such entries.
    body = [item for item in specs
            if item.get("kind") != "cover" and Path(str(item.get("filename") or "")).stem not in footer_stems]
    if len(body) < args.min:
        raise SystemExit(f"body image preflight failed: {len(body)} specs found, minimum is {args.min}")

    text = args.article.read_text(encoding="utf-8")
    matches = list(re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text))
    local_refs: dict[str, tuple[str, str]] = {}
    body_order: list[str] = []
    missing: list[str] = []
    all_stems: list[str] = []
    for match in matches:
        ref = match.group(1)
        if "://" in ref or ref.startswith("data:"):
            continue
        path = (args.article.parent / ref).resolve()
        if not path.is_file():
            missing.append(ref)
        stem = Path(ref).stem
        all_stems.append(stem)
        if stem in footer_stems:
            continue
        local_refs[stem] = (ref, _section_for_offset(text, match.start()))
        body_order.append(stem)
    if missing:
        raise SystemExit("body image preflight failed: missing references: " + ", ".join(missing))
    legacy = sorted(stem for stem in local_refs if stem in LEGACY_FOOTER_STEMS)
    if legacy:
        raise SystemExit(
            "body image preflight failed: legacy footer image " + ", ".join(legacy)
            + "; replace it with ~/Desktop/wechat/images/footer-guofeng-seal.png as the last image"
        )
    footer_positions = [i for i, stem in enumerate(all_stems) if stem in footer_stems]
    if footer_positions and footer_positions != [len(all_stems) - 1]:
        raise SystemExit(f"body image preflight failed: {footer_stem} must appear once, as the last image")

    spec_by_name = {str(item.get("filename") or ""): item for item in body}
    unplanned = sorted(name for name in local_refs if name not in spec_by_name)
    unreferenced = sorted(name for name in spec_by_name if name not in local_refs)
    if unplanned or unreferenced:
        raise SystemExit(
            "body image preflight failed: spec/reference mismatch; "
            f"unplanned refs={unplanned}, unreferenced specs={unreferenced}"
        )

    first_body = spec_by_name.get(body_order[0]) if body_order else None
    if first_body is None:
        raise SystemExit("body image preflight failed: no body image references found")
    first_backend = str(first_body.get("illustration_backend") or "")
    first_rendered_by = str(first_body.get("rendered_by") or "")
    track = _article_track(text)
    if track == "medical":
        # The medical opener must never be the Ian cartoon. A clinical AI
        # illustration or a deterministic opener is acceptable.
        if (
            first_backend == "ian-xiaohei-illustrations"
            or first_backend not in {"clinical-illustration", "html-render", "mermaid-render"}
            or first_body.get("render_mode") not in {"ai", "html", "mermaid"}
        ):
            raise SystemExit(
                "body image preflight failed: medical first body image must be a clinical illustration "
                f"or deterministic opener, not Ian Xiaohei; got {body_order[0]}"
            )
    elif not (
        first_body.get("render_mode") == "ai"
        and first_body.get("fact_risk") == "low"
        and first_backend == "ian-xiaohei-illustrations"
        and first_rendered_by.startswith("ian-xiaohei-illustrations")
    ):
        raise SystemExit(
            "body image preflight failed: first body image must be an Ian Xiaohei ai + low illustration; "
            f"got {body_order[0]}"
        )

    misplaced: list[str] = []
    for name, spec in spec_by_name.items():
        expected = str(spec.get("source_section") or "").strip()
        actual = local_refs[name][1]
        if expected and expected != actual:
            misplaced.append(f"{name}: expected '{expected}', found '{actual}'")
    if misplaced:
        raise SystemExit("body image preflight failed: misplaced images: " + "; ".join(misplaced))

    invalid_policy = [
        str(item.get("filename")) for item in body
        if item.get("render_mode") not in {"ai", "html", "hybrid", "mermaid"}
        or item.get("fact_risk") not in {"low", "medium", "high"}
    ]
    if invalid_policy:
        raise SystemExit(
            "body image preflight failed: invalid render policy: " + ", ".join(invalid_policy)
        )

    generic_labels: list[str] = []
    missing_locked_facts: list[str] = []
    for item in body:
        filename = str(item.get("filename") or "<unnamed>")
        labels = [str(label).strip() for label in item.get("labels") or []]
        labels.extend(
            str(value).strip()
            for card in item.get("items") or []
            for value in [card.get("label", "")]
            if str(value).strip()
        )
        hits = sorted({label for label in labels if _normalize_label(label) in GENERIC_LABELS})
        if hits:
            generic_labels.append(f"{filename}.labels={hits}")
        if (
            item.get("render_mode") in {"html", "hybrid", "mermaid"}
            and item.get("fact_risk") == "high"
            and not item.get("locked_facts")
        ):
            missing_locked_facts.append(f"{filename}.locked_facts")
    quality_errors: list[str] = []
    if generic_labels:
        quality_errors.append("generic labels: " + "; ".join(generic_labels))
    if missing_locked_facts:
        quality_errors.append("locked_facts required for high-risk HTML/hybrid specs: " + ", ".join(missing_locked_facts))
    if quality_errors:
        raise SystemExit("body image preflight failed: " + " | ".join(quality_errors))

    if track == "medical":
        misrouted_ai = [
            str(item.get("filename")) for item in body
            if item.get("render_mode") == "ai"
            and (
                item.get("illustration_backend") != "clinical-illustration"
                or not str(item.get("rendered_by") or "").startswith("clinical-illustration")
            )
        ]
        if misrouted_ai:
            raise SystemExit(
                "body image preflight failed: medical AI images were not rendered as clinical illustrations: "
                + ", ".join(misrouted_ai)
            )
    else:
        misrouted_ai = [
            str(item.get("filename")) for item in body
            if item.get("render_mode") == "ai"
            and item.get("fact_risk") == "low"
            and (
                item.get("illustration_backend") != "ian-xiaohei-illustrations"
                or not str(item.get("rendered_by") or "").startswith("ian-xiaohei-illustrations")
            )
        ]
        if misrouted_ai:
            raise SystemExit(
                "body image preflight failed: low-risk AI images were not rendered by Ian Xiaohei: "
                + ", ".join(misrouted_ai)
            )

    incomplete_hybrid = [
        str(item.get("filename")) for item in body
        if item.get("render_mode") == "hybrid" and not item.get("overlay_applied")
    ]
    if incomplete_hybrid:
        raise SystemExit(
            "body image preflight failed: hybrid images lack deterministic overlays: "
            + ", ".join(incomplete_hybrid)
        )

    misrouted_mermaid = [
        str(item.get("filename")) for item in body
        if item.get("render_mode") == "mermaid"
        and (
            item.get("illustration_backend") != "mermaid-render"
            or not str(item.get("rendered_by") or "").startswith("mermaid-render")
        )
    ]
    if misrouted_mermaid:
        raise SystemExit(
            "body image preflight failed: mermaid images were not rendered by the mermaid backend: "
            + ", ".join(misrouted_mermaid)
        )

    footer_note = ", footer present" if footer_positions else ", no footer image"
    print(f"body image preflight passed: {len(body)} specs, {len(local_refs)} mapped references{footer_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

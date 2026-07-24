#!/usr/bin/env python3
"""Fail preflight when body-image specs, files, and article placement diverge."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


GENERIC_LABELS = {"结果是什么", "要理解这个bug", "先说影响面"}


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
    args = parser.parse_args()

    specs = json.loads(args.spec.read_text(encoding="utf-8"))
    body = [item for item in specs if item.get("kind") != "cover"]
    if len(body) < args.min:
        raise SystemExit(f"body image preflight failed: {len(body)} specs found, minimum is {args.min}")

    text = args.article.read_text(encoding="utf-8")
    matches = list(re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text))
    local_refs: dict[str, tuple[str, str]] = {}
    body_order: list[str] = []
    missing: list[str] = []
    for match in matches:
        ref = match.group(1)
        if "://" in ref or ref.startswith("data:"):
            continue
        path = (args.article.parent / ref).resolve()
        if not path.is_file():
            missing.append(ref)
        stem = Path(ref).stem
        local_refs[stem] = (ref, _section_for_offset(text, match.start()))
        body_order.append(stem)
    if missing:
        raise SystemExit("body image preflight failed: missing references: " + ", ".join(missing))

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
    if not (
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
        if item.get("render_mode") not in {"ai", "html", "hybrid"}
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
            item.get("render_mode") in {"html", "hybrid"}
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

    print(f"body image preflight passed: {len(body)} specs, {len(local_refs)} mapped references")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

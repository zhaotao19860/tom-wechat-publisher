#!/usr/bin/env python3
"""Resolve per-account publishing profiles.

The skill historically assumed one WeChat account: one AppID, one author, one
theme, one history root and one footer. A second, independent account (for
example a medical account) must not share those values, because a wrong AppID
would upload a draft to the wrong official account and a shared history root
would mix topic de-duplication across two audiences.

A profile is a small JSON file under ``accounts/``. It never contains secrets:
only the *names* of the environment variables that hold the AppID/AppSecret and
the Keychain service label used as a fallback.

CLI used by publish_draft.sh::

    account_profile.py shell --profile medical
    account_profile.py show  --profile medical

``shell`` prints ``PROFILE_*=<shell-quoted value>`` lines for ``eval``.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

PROFILE_DIR = Path(__file__).resolve().parent.parent / "accounts"
DEFAULT_PROFILE = "default"

DEFAULT_VALUES: dict[str, Any] = {
    "id": DEFAULT_PROFILE,
    "display_name": "技术号",
    "track": "dns",
    "author": "赵涛",
    "theme": "dns-daily",
    "history_root": "~/Desktop/wechat",
    "app_id_env": "WECHAT_APP_ID",
    "app_secret_env": "WECHAT_APP_SECRET",
    "keychain_service": "tom-wechat-publisher",
    "album": "",
    "footer_image": "",
    "image_visual_theme": "",
    "preview_themes": [],
    "cover_styles": [],
}

_LIST_FIELDS = {"preview_themes", "cover_styles"}
_STRING_FIELDS = {
    "id",
    "display_name",
    "track",
    "author",
    "theme",
    "history_root",
    "app_id_env",
    "app_secret_env",
    "keychain_service",
    "album",
    "footer_image",
    "image_visual_theme",
}
_ALLOWED_FIELDS = _STRING_FIELDS | _LIST_FIELDS


class ProfileError(ValueError):
    """Raised when a profile is missing or malformed."""


def profile_path(name: str) -> Path:
    return PROFILE_DIR / f"{name}.json"


def _clean_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ProfileError(f"profile field {field} must be a string")
    return value.strip()


def _clean_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProfileError(f"profile field {field} must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def load_profile(name: str = DEFAULT_PROFILE) -> dict[str, Any]:
    """Load a profile, returning the built-in default when ``name`` is default.

    Unknown profile names fail closed: silently falling back to the technical
    account would risk uploading a medical draft to the wrong account.
    """
    name = (name or DEFAULT_PROFILE).strip()
    if name == DEFAULT_PROFILE:
        data = dict(DEFAULT_VALUES)
        data["history_root"] = os.path.expanduser(data["history_root"])
        return data

    path = profile_path(name)
    if not path.is_file():
        available = sorted(p.stem for p in PROFILE_DIR.glob("*.json"))
        hint = ", ".join(available) or "none"
        raise ProfileError(f"account profile not found: {path} (available: {hint})")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read profile {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ProfileError(f"profile {path} must contain a JSON object")
    unknown = sorted(set(raw) - _ALLOWED_FIELDS)
    if unknown:
        raise ProfileError(f"profile {path} has unknown fields: {', '.join(unknown)}")

    data = dict(DEFAULT_VALUES)
    data["id"] = name
    for field in _STRING_FIELDS:
        if field in raw:
            data[field] = _clean_string(raw[field], field)
    for field in _LIST_FIELDS:
        if field in raw:
            data[field] = _clean_list(raw[field], field)

    if not data["display_name"]:
        data["display_name"] = name
    if data["id"] != name:
        raise ProfileError(f"profile id {data['id']!r} does not match filename {name!r}")
    if not data["track"]:
        raise ProfileError("profile track is required")
    if not data["app_id_env"].replace("_", "").isalnum() or not data["app_id_env"].isupper():
        raise ProfileError("app_id_env must be an upper-case environment variable name")
    if not data["app_secret_env"].replace("_", "").isalnum() or not data["app_secret_env"].isupper():
        raise ProfileError("app_secret_env must be an upper-case environment variable name")
    if data["app_secret_env"] == data["app_id_env"]:
        raise ProfileError("app_id_env and app_secret_env must differ")

    data["history_root"] = os.path.expanduser(data["history_root"])
    return data


def shell_values(profile: dict[str, Any]) -> dict[str, str]:
    """Flatten a profile into shell-safe strings for publish_draft.sh."""
    values = {
        "PROFILE_ID": str(profile["id"]),
        "PROFILE_DISPLAY_NAME": str(profile["display_name"]),
        "PROFILE_TRACK": str(profile["track"]),
        "PROFILE_AUTHOR": str(profile["author"]),
        "PROFILE_THEME": str(profile["theme"]),
        "PROFILE_HISTORY_ROOT": str(profile["history_root"]),
        "PROFILE_APP_ID_ENV": str(profile["app_id_env"]),
        "PROFILE_APP_SECRET_ENV": str(profile["app_secret_env"]),
        "PROFILE_KEYCHAIN_SERVICE": str(profile["keychain_service"]),
        "PROFILE_ALBUM": str(profile["album"]),
        "PROFILE_FOOTER_IMAGE": str(profile["footer_image"]),
        "PROFILE_IMAGE_VISUAL_THEME": str(profile["image_visual_theme"]),
        "PROFILE_PREVIEW_THEMES": " ".join(str(item) for item in profile["preview_themes"]),
        "PROFILE_COVER_STYLES": ",".join(str(item) for item in profile["cover_styles"]),
    }
    return values


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Resolve a WeChat publishing account profile.")
    parser.add_argument("command", choices=("shell", "show"))
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    args = parser.parse_args(argv)

    try:
        profile = load_profile(args.profile)
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.command == "show":
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0

    for key, value in shell_values(profile).items():
        print(f"{key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
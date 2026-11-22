#!/usr/bin/env python3
"""image_backend.py - pluggable AI image generation for tom-wechat-publisher.

Drop-in replacement for create-image's generate-image.sh, with the same CLI:

    image_backend.py --prompt "..." --output name.png [--input ref.png]
                     [--resolution 1K|2K|4K]
                     [--aspect-ratio 1:1|16:9|9:16|4:3|3:4|3:2|2:3|21:9]
    image_backend.py --check [--json] [--account PROFILE]

The image is written to ./images/<output> and its absolute path is printed on
stdout. Failures print one tagged line per problem to stderr:
``[image-backend:<stage>] message``.

Backend selection, first match wins:
  1. env TOM_WECHAT_IMAGE_BACKEND
  2. field ``image_backend`` of the account profile (--account, else env
     TOM_WECHAT_ACCOUNT, else the built-in default profile)
  3. comate

Backends:
  comate   exec create-image's generate-image.sh unchanged. The script path can
           be overridden with TOM_WECHAT_CREATE_IMAGE_BASH (and
           TOM_WECHAT_CREATE_IMAGE_PS1 for the PowerShell variant).
  openai   OpenAI-compatible images API: POST {base}/images/generations, or
           {base}/images/edits (multipart) when --input is given.
           TOM_WECHAT_IMAGE_MODEL (required), TOM_WECHAT_IMAGE_BASE_URL
           (default https://api.openai.com/v1), TOM_WECHAT_IMAGE_SIZES (comma
           list of WxH sizes the model accepts; default
           1024x1024,1536x1024,1024x1536). The nearest size to the requested
           aspect ratio is sent and the ratio is also written into the prompt.
  gemini   Google Generative Language API: POST {base}/models/<model>:generateContent
           and read the inline image part. TOM_WECHAT_IMAGE_MODEL (required,
           e.g. gemini-3-pro-image-preview), TOM_WECHAT_IMAGE_BASE_URL (default
           https://generativelanguage.googleapis.com/v1beta).
  command  run TOM_WECHAT_IMAGE_COMMAND, an argv template with the placeholders
           {prompt} {output} {input} {aspect} {resolution}. It is split with
           shlex and run without a shell. {output} is the absolute target path
           the command must write. Without --input, every token that mentions
           {input} is dropped, and so is a bare flag right before a lone
           {input} token (``--image {input}`` disappears as a pair).

API key for openai/gemini: env TOM_WECHAT_IMAGE_API_KEY, else the macOS
Keychain item with service ``tom-wechat-publisher-image`` and account = host of
the base URL. Keys are never printed. Plain http base URLs are only accepted
for loopback hosts.

Timeout and retries: TOM_WECHAT_CREATE_IMAGE_TIMEOUT (seconds per request or
command run, default 75) and TOM_WECHAT_CREATE_IMAGE_ATTEMPTS (default 1).
Only network errors, HTTP 429 and HTTP 5xx are retried.

Exit codes (same meaning as generate-image.sh):
   0  success, absolute output path on stdout
   2  bad CLI args
   3  missing dependency (create-image wrapper, bash, command binary)
   4  missing/invalid configuration (backend name, profile, model, key, URL);
      --check also exits 4 when the selected backend is not usable
   5  input file missing / unreadable / empty
  10  HTTP/network failure or timeout
  11  API returned an error payload
  12  API response had no image data
  13  output file could not be written or is empty
The command backend passes the command's own non-zero exit code through.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import http.client
import json
import math
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
BACKENDS = ("comate", "openai", "gemini", "command")
ASPECTS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9")
RESOLUTIONS = ("1K", "2K", "4K")
RESOLUTION_EDGE = {"1K": 1024, "2K": 2048, "4K": 4096}
OUT_DIR = "images"
KEYCHAIN_SERVICE = "tom-wechat-publisher-image"
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
GEMINI_DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_DEFAULT_SIZES = "1024x1024,1536x1024,1024x1536"
PLACEHOLDER_RE = re.compile(r"\{(prompt|output|input|aspect|resolution)\}")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

EXIT_OK = 0
EXIT_ARGS = 2
EXIT_DEPS = 3
EXIT_ENV = 4
EXIT_INPUT = 5
EXIT_HTTP = 10
EXIT_API = 11
EXIT_NO_IMAGE = 12
EXIT_WRITE = 13

# Secrets registered here are masked in every message this module prints.
_SECRETS: list = []


class BackendError(Exception):
    def __init__(self, stage: str, message: str, code: int, retryable: bool = False) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.retryable = retryable


@dataclass
class HttpConfig:
    base: str
    model: str
    key: str


def redact(text: str) -> str:
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "***")
    return text


def err(stage: str, message: str) -> None:
    print(f"[image-backend:{stage}] {redact(message)}", file=sys.stderr)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _int_env(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(_env(name, str(default))))
    except ValueError:
        return default


def _timeout() -> float:
    return float(_int_env("TOM_WECHAT_CREATE_IMAGE_TIMEOUT", 75, 1))


# ---- comate (create-image) --------------------------------------------------


def comate_script(env_name: str = "TOM_WECHAT_CREATE_IMAGE_BASH", filename: str = "generate-image.sh") -> Path:
    """Locate the create-image wrapper: explicit override, then known skill roots."""
    override = _env(env_name)
    if override:
        return Path(override).expanduser()
    candidates = [
        Path.home() / root / "skills" / ".system" / "create-image" / "scripts" / filename
        for root in (".comate", ".agents", ".codex")
    ]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def _decode_jwt_payload(token: str) -> dict:
    parts = token.strip().split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except Exception:
        return {}


def resolve_comate_username_encrypted() -> str:
    """Find the Comate encrypted username that create-image needs.

    Some Comate terminal sessions do not inherit COMATE_USERNAME_ENCRYPTED.
    Comate stores a bearer token under ~/.comate/login-user/<username>; its JWT
    payload carries content.identity, which is the encrypted username. Never
    print the token or the identity value.
    """
    existing = os.environ.get("COMATE_USERNAME_ENCRYPTED", "").strip()
    if existing:
        return existing

    login_dir = Path.home() / ".comate" / "login-user"
    candidates = []
    if login_dir.exists():
        candidates.extend(p for p in login_dir.iterdir() if p.is_file())
    fallback_login = Path.home() / ".comate" / "login"
    if fallback_login.exists():
        candidates.append(fallback_login)

    for candidate in candidates:
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        raw_candidates = [raw]
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                raw_candidates.extend(
                    str(value)
                    for key, value in parsed.items()
                    if isinstance(value, str) and key.lower() in {"token", "access_token", "authorization", "bearer", "bearertoken"}
                )
        except Exception:
            pass
        for raw_token in raw_candidates:
            bearer_match = re.search(r"Bearer[-\s]+([A-Za-z0-9_.-]+)", raw_token)
            token = bearer_match.group(1) if bearer_match else raw_token
            if token.startswith("Bearer-"):
                token = token[len("Bearer-") :]
            payload = _decode_jwt_payload(token)
            identity = (payload.get("content") or {}).get("identity")
            if isinstance(identity, str) and identity:
                return identity
    return ""


# ---- backend selection and configuration ------------------------------------


def _profile_backend(account: str) -> str:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    import account_profile  # type: ignore

    try:
        profile = account_profile.load_profile(account)
    except account_profile.ProfileError as exc:
        raise BackendError("profile", str(exc), EXIT_ENV) from exc
    return str(profile.get("image_backend") or "").strip().lower()


def select_backend(account: Optional[str] = None) -> tuple:
    """Return (backend, source) following env > profile > comate."""
    name = _env("TOM_WECHAT_IMAGE_BACKEND").lower()
    source = "env TOM_WECHAT_IMAGE_BACKEND"
    if not name:
        account = (account or _env("TOM_WECHAT_ACCOUNT") or "default").strip()
        name = _profile_backend(account)
        source = f"profile {account}.image_backend"
    if not name:
        name, source = "comate", "default"
    if name not in BACKENDS:
        raise BackendError(
            "config",
            f"unknown image backend {name!r} from {source} (expect {'|'.join(BACKENDS)})",
            EXIT_ENV,
        )
    return name, source


def base_url(backend: str) -> str:
    default = GEMINI_DEFAULT_BASE if backend == "gemini" else OPENAI_DEFAULT_BASE
    return _env("TOM_WECHAT_IMAGE_BASE_URL", default).rstrip("/")


def base_url_problem(base: str) -> str:
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme == "https" and parsed.hostname:
        return ""
    if parsed.scheme == "http" and (parsed.hostname or "") in LOOPBACK_HOSTS:
        return ""
    return f"TOM_WECHAT_IMAGE_BASE_URL must be an https URL (http only for localhost): {base}"


def resolve_api_key(base: str) -> tuple:
    """Return (key, source). Source names where the key came from, never the key."""
    key = _env("TOM_WECHAT_IMAGE_API_KEY")
    if key:
        return key, "env TOM_WECHAT_IMAGE_API_KEY"
    host = urllib.parse.urlparse(base).hostname or ""
    security = shutil.which("security")
    if host and security:
        try:
            result = subprocess.run(
                [security, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", host, "-w"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), f"keychain {KEYCHAIN_SERVICE}/{host}"
    return "", ""


def _which(binary: str) -> Optional[str]:
    if os.sep in binary or (os.altsep and os.altsep in binary):
        path = Path(binary).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(binary)


def check(account: Optional[str] = None) -> dict:
    """Report the selected backend and whether it can run. Never includes keys."""
    report: dict = {"backend": None, "source": None, "usable": False, "details": {}, "problems": []}
    try:
        name, source = select_backend(account)
    except BackendError as exc:
        report["problems"].append(str(exc))
        return report
    report["backend"], report["source"] = name, source
    details, problems = report["details"], report["problems"]
    if name == "comate":
        script = comate_script()
        details["script"] = str(script)
        if not script.is_file():
            ps1 = comate_script("TOM_WECHAT_CREATE_IMAGE_PS1", "generate-image.ps1")
            if ps1.is_file():
                details["script"] = str(ps1)
            else:
                problems.append(f"create-image wrapper not found: {script}")
        has_identity = bool(resolve_comate_username_encrypted())
        details["identity"] = "present" if has_identity else "missing"
        if not has_identity:
            problems.append("COMATE_USERNAME_ENCRYPTED not in env and no usable token under ~/.comate/login-user")
    elif name in ("openai", "gemini"):
        base = base_url(name)
        model = _env("TOM_WECHAT_IMAGE_MODEL")
        details["base_url"] = base
        details["model"] = model
        url_problem = base_url_problem(base)
        if url_problem:
            problems.append(url_problem)
        if not model:
            problems.append("TOM_WECHAT_IMAGE_MODEL is not set")
        key, key_source = resolve_api_key(base)
        details["key"] = f"present ({key_source})" if key else "missing"
        if not key:
            host = urllib.parse.urlparse(base).hostname or "<host>"
            problems.append(
                f"no API key: set TOM_WECHAT_IMAGE_API_KEY or add Keychain item service={KEYCHAIN_SERVICE} account={host}"
            )
    else:
        template = _env("TOM_WECHAT_IMAGE_COMMAND")
        try:
            tokens = shlex.split(template)
        except ValueError as exc:
            tokens = []
            problems.append(f"cannot parse TOM_WECHAT_IMAGE_COMMAND: {exc}")
        if not template:
            problems.append("TOM_WECHAT_IMAGE_COMMAND is not set")
        elif tokens:
            # Only the binary is reported: the rest of the template may carry secrets.
            binary = _which(tokens[0])
            details["binary"] = binary or tokens[0]
            if not binary:
                problems.append(f"command not found: {tokens[0]}")
            if "{output}" not in template:
                problems.append("TOM_WECHAT_IMAGE_COMMAND must contain {output}")
    report["usable"] = not problems
    return report


def format_check(report: dict) -> str:
    lines = [
        f"backend: {report.get('backend') or 'unknown'} ({report.get('source') or 'unresolved'})",
        f"usable: {'yes' if report.get('usable') else 'no'}",
    ]
    for key, value in (report.get("details") or {}).items():
        lines.append(f"{key}: {value}")
    for problem in report.get("problems") or []:
        lines.append(f"problem: {problem}")
    return redact("\n".join(lines))


# ---- HTTP helpers -----------------------------------------------------------


def _urlopen(request: urllib.request.Request, timeout: float):
    """Single network entry point; tests replace it with a fake."""
    return urllib.request.urlopen(request, timeout=timeout)


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))


def http_request(url: str, data: Optional[bytes], headers: dict, method: str = "POST") -> tuple:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _urlopen(request, _timeout()) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read() or b""
        except Exception:
            body = b""
        return exc.code, body
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise BackendError("http", f"request to {_safe_url(url)} failed: {reason}", EXIT_HTTP, retryable=True) from exc


def _json_or_none(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _error_message(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not error:
        return ""
    if isinstance(error, dict):
        return str(error.get("message") or json.dumps(error, ensure_ascii=False))
    return str(error)


def _snippet(body: bytes, limit: int = 500) -> str:
    return body[:limit].decode("utf-8", "replace").replace("\n", " ")


def _check_status(status: int, body: bytes, url: str) -> None:
    if 200 <= status < 300:
        return
    message = _error_message(_json_or_none(body)) or _snippet(body)
    raise BackendError(
        "http",
        f"HTTP {status} from {_safe_url(url)}: {message[:500]}",
        EXIT_HTTP,
        retryable=status == 429 or status >= 500,
    )


def _json_payload(body: bytes) -> dict:
    payload = _json_or_none(body)
    if not isinstance(payload, dict):
        raise BackendError("api", f"response is not a JSON object: {_snippet(body)}", EXIT_NO_IMAGE)
    message = _error_message(payload)
    if message:
        raise BackendError("api", f"server returned error: {message[:1000]}", EXIT_API)
    return payload


def _b64decode(data: str) -> bytes:
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data)
    except (binascii.Error, ValueError) as exc:
        raise BackendError("api", f"image data is not valid base64: {exc}", EXIT_NO_IMAGE) from exc


def with_retries(func: Callable[[], bytes]) -> bytes:
    attempts = _int_env("TOM_WECHAT_CREATE_IMAGE_ATTEMPTS", 1, 1)
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except BackendError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            err(exc.stage, f"attempt {attempt}/{attempts} failed, retrying: {exc}")
            time.sleep(min(2 * attempt, 10))
    raise AssertionError("unreachable")


# ---- prompt and size helpers ------------------------------------------------


def _aspect_value(aspect: str) -> float:
    width, height = aspect.split(":")
    return int(width) / int(height)


def aspect_prompt(prompt: str, aspect: str) -> str:
    """Spell the ratio out in the prompt; many models ignore size parameters."""
    ratio = _aspect_value(aspect)
    shape = "square" if ratio == 1 else ("landscape" if ratio > 1 else "portrait")
    return f"{prompt.rstrip()}\n\nCompose the image for a {aspect} aspect ratio ({shape} canvas)."


def parse_sizes(raw: str) -> list:
    sizes = []
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        match = re.fullmatch(r"(\d+)x(\d+)", item)
        if not match or int(match.group(1)) == 0 or int(match.group(2)) == 0:
            raise BackendError("config", f"invalid size {item!r} in TOM_WECHAT_IMAGE_SIZES (expect WxH)", EXIT_ENV)
        sizes.append((int(match.group(1)), int(match.group(2))))
    if not sizes:
        raise BackendError("config", "TOM_WECHAT_IMAGE_SIZES is empty", EXIT_ENV)
    return sizes


def openai_size(aspect: str, resolution: str, raw_sizes: Optional[str] = None) -> str:
    """Pick the supported size closest in aspect ratio, then in long edge."""
    sizes = parse_sizes(raw_sizes if raw_sizes is not None else _env("TOM_WECHAT_IMAGE_SIZES", OPENAI_DEFAULT_SIZES))
    target = math.log(_aspect_value(aspect))
    edge = RESOLUTION_EDGE.get(resolution, 1024)
    width, height = min(
        sizes,
        key=lambda size: (round(abs(math.log(size[0] / size[1]) - target), 6), abs(max(size) - edge)),
    )
    return f"{width}x{height}"


def _mime_for(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or ""
    return mime if mime.startswith("image/") else "image/png"


def encode_multipart(fields: dict, files: list) -> tuple:
    """Build a multipart/form-data body. files: [(field, filename, mime, bytes)]."""
    boundary = "----tomwechat" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    for name, filename, mime, data in files:
        safe_name = re.sub(r'["\r\n]', "_", filename)
        header = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{safe_name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        )
        chunks.append(header.encode("utf-8") + data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


# ---- openai -----------------------------------------------------------------


def openai_generate(opts: argparse.Namespace, cfg: HttpConfig) -> bytes:
    size = openai_size(opts.aspect, opts.resolution)
    prompt = aspect_prompt(opts.prompt, opts.aspect)
    headers = {"Authorization": f"Bearer {cfg.key}"}
    if opts.input:
        url = f"{cfg.base}/images/edits"
        source = Path(opts.input)
        body, content_type = encode_multipart(
            {"model": cfg.model, "prompt": prompt, "size": size, "n": "1"},
            [("image", source.name, _mime_for(source), source.read_bytes())],
        )
    else:
        url = f"{cfg.base}/images/generations"
        body = json.dumps({"model": cfg.model, "prompt": prompt, "size": size, "n": 1}).encode("utf-8")
        content_type = "application/json"
    headers["Content-Type"] = content_type
    status, response = http_request(url, body, headers)
    _check_status(status, response, url)
    payload = _json_payload(response)
    items = payload.get("data") or []
    first = items[0] if items and isinstance(items[0], dict) else {}
    if first.get("b64_json"):
        return _b64decode(str(first["b64_json"]))
    image_url = str(first.get("url") or "")
    if image_url:
        if urllib.parse.urlparse(image_url).scheme not in ("https", "http"):
            raise BackendError("api", "image url is not http(s)", EXIT_NO_IMAGE)
        # Hosted image URLs are pre-signed; do not send the API key to them.
        status, data = http_request(image_url, None, {}, method="GET")
        _check_status(status, data, image_url)
        return data
    raise BackendError("api", f"no b64_json or url in response: {_snippet(response)}", EXIT_NO_IMAGE)


# ---- gemini -----------------------------------------------------------------


def _gemini_body(opts: argparse.Namespace, include_size: bool) -> bytes:
    parts = []
    if opts.input:
        source = Path(opts.input)
        parts.append(
            {"inline_data": {"mime_type": _mime_for(source), "data": base64.b64encode(source.read_bytes()).decode("ascii")}}
        )
    parts.append({"text": aspect_prompt(opts.prompt, opts.aspect)})
    image_config = {"aspectRatio": opts.aspect}
    if include_size:
        image_config["imageSize"] = opts.resolution
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": image_config},
    }
    return json.dumps(payload).encode("utf-8")


def gemini_generate(opts: argparse.Namespace, cfg: HttpConfig) -> bytes:
    model = cfg.model[len("models/") :] if cfg.model.startswith("models/") else cfg.model
    url = f"{cfg.base}/models/{urllib.parse.quote(model, safe='-._')}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": cfg.key}
    status, response = http_request(url, _gemini_body(opts, include_size=True), headers)
    if status == 400 and re.search(r"image_?size", _snippet(response, 2000), re.IGNORECASE):
        # Older image models (gemini-2.5-flash-image) reject imageSize.
        status, response = http_request(url, _gemini_body(opts, include_size=False), headers)
    _check_status(status, response, url)
    payload = _json_payload(response)
    texts = []
    for candidate in payload.get("candidates") or []:
        for part in ((candidate or {}).get("content") or {}).get("parts") or []:
            if not isinstance(part, dict) or part.get("thought"):
                continue  # thinking models also return draft images marked thought
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return _b64decode(str(inline["data"]))
            if part.get("text"):
                texts.append(str(part["text"]))
    block = (payload.get("promptFeedback") or {}).get("blockReason")
    if block:
        reason = f"prompt blocked: {block}"
    elif texts:
        reason = f"model said: {' '.join(texts)[:500]}"
    else:
        reason = f"body: {_snippet(response)}"
    raise BackendError("api", f"no inline image in response; {reason}", EXIT_NO_IMAGE)


# ---- command ----------------------------------------------------------------


def command_argv(template: str, values: dict, has_input: bool) -> list:
    try:
        tokens = shlex.split(template)
    except ValueError as exc:
        raise BackendError("config", f"cannot parse TOM_WECHAT_IMAGE_COMMAND: {exc}", EXIT_ENV) from exc
    if not tokens:
        raise BackendError("config", "TOM_WECHAT_IMAGE_COMMAND is not set", EXIT_ENV)
    argv = []
    previous_is_flag = False  # last kept template token was a literal "-x"/"--xx"
    for token in tokens:
        if not has_input and "{input}" in token:
            if token == "{input}" and previous_is_flag:
                argv.pop()  # the flag that introduced the missing input
            previous_is_flag = False
            continue
        previous_is_flag = len(argv) > 0 and token.startswith("-") and not PLACEHOLDER_RE.search(token)
        # One pass, so placeholder-like text inside the prompt is left alone.
        argv.append(PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], token))
    return argv


def run_command(opts: argparse.Namespace, out_path: Path) -> int:
    values = {
        "prompt": opts.prompt,
        "output": str(out_path),
        "input": str(Path(opts.input).resolve()) if opts.input else "",
        "aspect": opts.aspect,
        "resolution": opts.resolution,
    }
    argv = command_argv(_env("TOM_WECHAT_IMAGE_COMMAND"), values, bool(opts.input))
    binary = _which(argv[0])
    if not binary:
        raise BackendError("deps", f"command not found: {argv[0]}", EXIT_DEPS)
    argv[0] = binary
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, timeout=_timeout())
    except subprocess.TimeoutExpired as exc:
        raise BackendError("command", f"timed out after {_timeout():.0f}s", EXIT_HTTP) from exc
    except OSError as exc:
        raise BackendError("command", f"cannot run {argv[0]}: {exc}", EXIT_DEPS) from exc
    if result.stdout:
        # Our stdout carries only the output path; forward the tool's chatter to stderr.
        sys.stderr.write(redact(result.stdout.decode("utf-8", "replace")))
    return result.returncode


# ---- comate -----------------------------------------------------------------


def run_comate(opts: argparse.Namespace) -> int:
    """Hand the request to create-image unchanged. On POSIX this execs and never returns."""
    script = comate_script()
    if script.is_file():
        bash = shutil.which("bash")
        if not bash:
            raise BackendError("deps", "required binary not found: bash", EXIT_DEPS)
        cmd = [bash, str(script), "--prompt", opts.prompt, "--output", opts.output]
        if opts.input:
            cmd += ["--input", opts.input]
        if opts.resolution:
            cmd += ["--resolution", opts.resolution]
        cmd += ["--aspect-ratio", opts.aspect]
        use_exec = os.name == "posix"
    else:
        ps1 = comate_script("TOM_WECHAT_CREATE_IMAGE_PS1", "generate-image.ps1")
        if not ps1.is_file():
            raise BackendError(
                "deps",
                f"create-image wrapper not found: {script}. Without Comate, set TOM_WECHAT_IMAGE_BACKEND=openai|gemini|command",
                EXIT_DEPS,
            )
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if not shell:
            raise BackendError("deps", "required binary not found: powershell", EXIT_DEPS)
        cmd = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
               "-Prompt", opts.prompt, "-Output", opts.output, "-AspectRatio", opts.aspect]
        if opts.resolution:
            cmd += ["-Resolution", opts.resolution]
        if opts.input:
            cmd += ["-InputPath", opts.input]
        use_exec = False
    env = os.environ.copy()
    if not env.get("COMATE_USERNAME_ENCRYPTED", "").strip():
        identity = resolve_comate_username_encrypted()
        if identity:
            env["COMATE_USERNAME_ENCRYPTED"] = identity
    sys.stdout.flush()
    sys.stderr.flush()
    if use_exec:
        os.execve(cmd[0], cmd, env)
    return subprocess.run(cmd, env=env).returncode


# ---- input / output ---------------------------------------------------------


def validate_input(path: str) -> None:
    source = Path(path)
    if not source.is_file():
        raise BackendError("input", f"input image not found: {path}", EXIT_INPUT)
    if not os.access(source, os.R_OK):
        raise BackendError("input", f"input image not readable: {path}", EXIT_INPUT)
    if source.stat().st_size == 0:
        raise BackendError("input", f"input image is empty: {path}", EXIT_INPUT)


def detect_resolution(path: str) -> str:
    """Mirror generate-image.sh: pick 1K/2K/4K from the input's longest edge."""
    sips = shutil.which("sips")
    if not sips:
        return "1K"
    try:
        result = subprocess.run([sips, "-g", "pixelWidth", "-g", "pixelHeight", path], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return "1K"
    dims = [int(value) for value in re.findall(r"pixel(?:Width|Height):\s*(\d+)", result.stdout)]
    longest = max(dims) if dims else 0
    return "4K" if longest >= 3000 else ("2K" if longest >= 1500 else "1K")


def prepare_output(output: str) -> Path:
    out_dir = Path(OUT_DIR)
    if out_dir.exists() and not out_dir.is_dir():
        raise BackendError("output", f"'{OUT_DIR}' exists but is not a directory", EXIT_WRITE)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackendError("output", f"failed to create output dir {OUT_DIR}: {exc}", EXIT_WRITE) from exc
    return (out_dir / output).resolve()


def write_image(path: Path, data: bytes) -> None:
    if not data:
        raise BackendError("write", "decoded image is empty", EXIT_WRITE)
    try:
        path.write_bytes(data)
    except OSError as exc:
        raise BackendError("write", f"cannot write {path}: {exc}", EXIT_WRITE) from exc


# ---- CLI --------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        err("args", message)
        raise SystemExit(EXIT_ARGS)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="image_backend.py",
        description=__doc__.split("\n\n", 1)[0] if __doc__ else None,
        epilog="See the module docstring (or SKILL.md, section 图片后端) for backends and env vars.",
    )
    parser.add_argument("-p", "--prompt", default="")
    parser.add_argument("-o", "--output", default="", help="filename only; written under ./images/")
    parser.add_argument("-i", "--input", default="", help="reference image for image-to-image")
    parser.add_argument("-r", "--resolution", default="", help="1K|2K|4K")
    parser.add_argument("-a", "--aspect-ratio", dest="aspect", default="1:1", help="|".join(ASPECTS))
    parser.add_argument("--check", action="store_true", help="report the selected backend and whether it is usable")
    parser.add_argument("--json", action="store_true", help="with --check, print JSON")
    parser.add_argument("--account", default="", help="account profile used for image_backend (default: $TOM_WECHAT_ACCOUNT)")
    return parser


def validate_args(opts: argparse.Namespace) -> None:
    if not opts.prompt:
        raise BackendError("args", "--prompt is required", EXIT_ARGS)
    if not opts.output:
        raise BackendError("args", "--output is required", EXIT_ARGS)
    if "/" in opts.output or os.sep in opts.output:
        raise BackendError("args", f"--output must be a filename only, not a path: {opts.output}", EXIT_ARGS)
    if opts.resolution and opts.resolution not in RESOLUTIONS:
        raise BackendError("args", f"invalid --resolution: {opts.resolution} (expect 1K|2K|4K)", EXIT_ARGS)
    if opts.aspect not in ASPECTS:
        raise BackendError("args", f"invalid --aspect-ratio: {opts.aspect}", EXIT_ARGS)


def http_config(backend: str) -> HttpConfig:
    base = base_url(backend)
    problem = base_url_problem(base)
    if problem:
        raise BackendError("env", problem, EXIT_ENV)
    model = _env("TOM_WECHAT_IMAGE_MODEL")
    if not model:
        raise BackendError("env", f"TOM_WECHAT_IMAGE_MODEL is not set for the {backend} backend", EXIT_ENV)
    key, _source = resolve_api_key(base)
    if not key:
        host = urllib.parse.urlparse(base).hostname or "<host>"
        raise BackendError(
            "env",
            f"no API key: set TOM_WECHAT_IMAGE_API_KEY or add Keychain item service={KEYCHAIN_SERVICE} account={host}",
            EXIT_ENV,
        )
    _SECRETS.append(key)
    return HttpConfig(base=base, model=model, key=key)


def generate(opts: argparse.Namespace) -> int:
    validate_args(opts)
    backend, _source = select_backend(opts.account or None)
    if backend == "comate":
        return run_comate(opts)
    if opts.input:
        validate_input(opts.input)
    if not opts.resolution:
        opts.resolution = detect_resolution(opts.input) if opts.input else "1K"
    if backend == "command":
        out_path = prepare_output(opts.output)
        if out_path.exists():
            out_path.unlink()  # a stale file must not pass for fresh output
        code = run_command(opts, out_path)
        if code != 0:
            err("command", f"command exited with status {code}")
            return code
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise BackendError("write", f"command succeeded but did not write {out_path}", EXIT_WRITE)
    else:
        cfg = http_config(backend)
        func = openai_generate if backend == "openai" else gemini_generate
        data = with_retries(lambda: func(opts, cfg))
        out_path = prepare_output(opts.output)
        write_image(out_path, data)
    print(str(out_path))
    return EXIT_OK


def main(argv: Optional[list] = None) -> int:
    opts = build_parser().parse_args(argv)
    if opts.check:
        report = check(opts.account or None)
        print(redact(json.dumps(report, ensure_ascii=False)) if opts.json else format_check(report))
        return EXIT_OK if report["usable"] else EXIT_ENV
    try:
        return generate(opts)
    except BackendError as exc:
        err(exc.stage, str(exc))
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())

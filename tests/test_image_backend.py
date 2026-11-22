from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BACKEND = SCRIPTS / "image_backend.py"
sys.path.insert(0, str(SCRIPTS))

import account_profile  # noqa: E402
import image_backend  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PNG_B64 = base64.b64encode(PNG).decode("ascii")
SECRET = "sk-test-secret-value-123"

# Every variable the backend reads; cleared so the developer's shell cannot leak in.
BACKEND_ENV = (
    "TOM_WECHAT_IMAGE_BACKEND",
    "TOM_WECHAT_IMAGE_MODEL",
    "TOM_WECHAT_IMAGE_BASE_URL",
    "TOM_WECHAT_IMAGE_API_KEY",
    "TOM_WECHAT_IMAGE_COMMAND",
    "TOM_WECHAT_IMAGE_SIZES",
    "TOM_WECHAT_ACCOUNT",
    "TOM_WECHAT_CREATE_IMAGE_ATTEMPTS",
    "TOM_WECHAT_CREATE_IMAGE_TIMEOUT",
    "TOM_WECHAT_CREATE_IMAGE_BASH",
    "TOM_WECHAT_CREATE_IMAGE_PS1",
    "COMATE_USERNAME_ENCRYPTED",
)


def clean_env(**extra: str) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in BACKEND_ENV}
    env.update(extra)
    return env


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeHttp:
    """Stand-in for image_backend._urlopen that records each request."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        status, body = self.responses.pop(0)
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(body))
        return FakeResponse(status, body)


class BackendTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.work = self.tmp / "work"
        self.work.mkdir()
        cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)
        env_patch = mock.patch.dict(os.environ, clean_env(HOME=str(self.tmp / "home")), clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        # Never touch the real Keychain from in-process tests.
        keychain = mock.patch.object(image_backend, "KEYCHAIN_SERVICE", "tom-wechat-publisher-image-unit-test")
        keychain.start()
        self.addCleanup(keychain.stop)
        image_backend._SECRETS.clear()

    def run_main(self, *argv: str) -> tuple:
        out, errs = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errs):
            code = image_backend.main(list(argv))
        return code, out.getvalue(), errs.getvalue()

    def make_profile(self, name: str, **fields: str) -> None:
        profiles = self.tmp / "accounts"
        profiles.mkdir(exist_ok=True)
        data = {"id": name, "track": "dns", **fields}
        (profiles / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
        patcher = mock.patch.object(account_profile, "PROFILE_DIR", profiles)
        patcher.start()
        self.addCleanup(patcher.stop)


class SelectionTests(BackendTestCase):
    def test_default_is_comate(self) -> None:
        self.assertEqual(image_backend.select_backend(), ("comate", "default"))

    def test_medical_profile_leaves_backend_unset(self) -> None:
        self.assertEqual(image_backend.select_backend("medical")[0], "comate")

    def test_profile_field_selects_backend(self) -> None:
        self.make_profile("home", image_backend="gemini")
        self.assertEqual(image_backend.select_backend("home"), ("gemini", "profile home.image_backend"))

    def test_account_env_is_used_without_account_flag(self) -> None:
        self.make_profile("home", image_backend="openai")
        os.environ["TOM_WECHAT_ACCOUNT"] = "home"
        self.assertEqual(image_backend.select_backend()[0], "openai")

    def test_env_overrides_profile(self) -> None:
        self.make_profile("home", image_backend="gemini")
        os.environ["TOM_WECHAT_IMAGE_BACKEND"] = "Command"
        self.assertEqual(image_backend.select_backend("home"), ("command", "env TOM_WECHAT_IMAGE_BACKEND"))

    def test_unknown_backend_is_config_error(self) -> None:
        os.environ["TOM_WECHAT_IMAGE_BACKEND"] = "dalle"
        with self.assertRaises(image_backend.BackendError) as ctx:
            image_backend.select_backend()
        self.assertEqual(ctx.exception.code, image_backend.EXIT_ENV)

    def test_unknown_profile_is_config_error(self) -> None:
        with self.assertRaises(image_backend.BackendError) as ctx:
            image_backend.select_backend("does-not-exist")
        self.assertEqual(ctx.exception.code, image_backend.EXIT_ENV)

    def test_invalid_profile_backend_is_rejected(self) -> None:
        self.make_profile("bad", image_backend="midjourney")
        with self.assertRaises(account_profile.ProfileError):
            account_profile.load_profile("bad")


class SizeAndPromptTests(unittest.TestCase):
    def test_nearest_default_size(self) -> None:
        self.assertEqual(image_backend.openai_size("16:9", "2K", image_backend.OPENAI_DEFAULT_SIZES), "1536x1024")
        self.assertEqual(image_backend.openai_size("21:9", "2K", image_backend.OPENAI_DEFAULT_SIZES), "1536x1024")
        self.assertEqual(image_backend.openai_size("9:16", "1K", image_backend.OPENAI_DEFAULT_SIZES), "1024x1536")
        self.assertEqual(image_backend.openai_size("1:1", "1K", image_backend.OPENAI_DEFAULT_SIZES), "1024x1024")

    def test_resolution_breaks_ratio_ties(self) -> None:
        sizes = "1024x1024,2048x2048,1792x1024"
        self.assertEqual(image_backend.openai_size("1:1", "2K", sizes), "2048x2048")
        self.assertEqual(image_backend.openai_size("1:1", "1K", sizes), "1024x1024")
        self.assertEqual(image_backend.openai_size("16:9", "1K", sizes), "1792x1024")

    def test_invalid_sizes_are_config_errors(self) -> None:
        with self.assertRaises(image_backend.BackendError) as ctx:
            image_backend.openai_size("1:1", "1K", "big")
        self.assertEqual(ctx.exception.code, image_backend.EXIT_ENV)

    def test_ratio_is_written_into_prompt(self) -> None:
        text = image_backend.aspect_prompt("A lighthouse", "16:9")
        self.assertTrue(text.startswith("A lighthouse"))
        self.assertIn("16:9 aspect ratio (landscape", text)
        self.assertIn("portrait", image_backend.aspect_prompt("x", "3:4"))


class OpenAITests(BackendTestCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ.update(
            TOM_WECHAT_IMAGE_BACKEND="openai",
            TOM_WECHAT_IMAGE_MODEL="gpt-image-1",
            TOM_WECHAT_IMAGE_BASE_URL="https://api.example.com/v1/",
            TOM_WECHAT_IMAGE_API_KEY=SECRET,
        )

    def test_generation_request_shape(self) -> None:
        fake = FakeHttp((200, {"data": [{"b64_json": PNG_B64}]}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, out, errs = self.run_main("--prompt", "A DNS resolver", "--output", "cover.png", "--aspect-ratio", "16:9", "--resolution", "2K")
        self.assertEqual(code, 0, errs)
        request = fake.requests[0]
        self.assertEqual(request.full_url, "https://api.example.com/v1/images/generations")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {SECRET}")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "gpt-image-1")
        self.assertEqual(body["size"], "1536x1024")
        self.assertEqual(body["n"], 1)
        self.assertIn("A DNS resolver", body["prompt"])
        self.assertIn("16:9 aspect ratio", body["prompt"])
        target = (self.work / "images" / "cover.png").resolve()
        self.assertEqual(out.strip(), str(target))
        self.assertEqual(target.read_bytes(), PNG)
        self.assertNotIn(SECRET, out + errs)

    def test_edit_is_multipart_and_url_response_is_downloaded(self) -> None:
        source = self.work / "ref.png"
        source.write_bytes(PNG)
        fake = FakeHttp(
            (200, {"data": [{"url": "https://cdn.example.com/out.png?sig=abc"}]}),
            (200, PNG),
        )
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, out, errs = self.run_main(
                "--prompt", "fix labels", "--output", "fixed.png", "--input", str(source),
                "--aspect-ratio", "9:16", "--resolution", "1K",
            )
        self.assertEqual(code, 0, errs)
        edit, download = fake.requests
        self.assertEqual(edit.full_url, "https://api.example.com/v1/images/edits")
        content_type = edit.get_header("Content-type")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        boundary = content_type.split("boundary=", 1)[1].encode()
        self.assertTrue(edit.data.startswith(b"--" + boundary))
        self.assertTrue(edit.data.endswith(b"--" + boundary + b"--\r\n"))
        self.assertIn(b'name="model"\r\n\r\ngpt-image-1\r\n', edit.data)
        self.assertIn(b'name="size"\r\n\r\n1024x1536\r\n', edit.data)
        self.assertIn(b'name="image"; filename="ref.png"\r\nContent-Type: image/png\r\n\r\n' + PNG, edit.data)
        self.assertIn(b"9:16 aspect ratio", edit.data)
        self.assertEqual(download.get_method(), "GET")
        self.assertEqual(download.full_url, "https://cdn.example.com/out.png?sig=abc")
        self.assertIsNone(download.get_header("Authorization"))
        self.assertEqual((self.work / "images" / "fixed.png").read_bytes(), PNG)

    def test_error_payload_exits_11(self) -> None:
        fake = FakeHttp((200, {"error": {"message": "content policy"}}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, image_backend.EXIT_API)
        self.assertIn("content policy", errs)

    def test_missing_image_exits_12(self) -> None:
        fake = FakeHttp((200, {"data": []}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, _errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, image_backend.EXIT_NO_IMAGE)
        self.assertFalse((self.work / "images" / "a.png").exists())

    def test_http_error_exits_10_and_redacts_key(self) -> None:
        fake = FakeHttp((401, {"error": {"message": f"bad key {SECRET}"}}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, out, errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, image_backend.EXIT_HTTP)
        self.assertIn("HTTP 401", errs)
        self.assertNotIn(SECRET, out + errs)
        self.assertEqual(len(fake.requests), 1, "4xx must not be retried")

    def test_5xx_is_retried_per_attempts_env(self) -> None:
        os.environ["TOM_WECHAT_CREATE_IMAGE_ATTEMPTS"] = "2"
        fake = FakeHttp((503, b"busy"), (200, {"data": [{"b64_json": PNG_B64}]}))
        with mock.patch.object(image_backend, "_urlopen", fake), mock.patch.object(image_backend.time, "sleep"):
            code, _out, errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, 0, errs)
        self.assertEqual(len(fake.requests), 2)

    def test_timeout_env_reaches_urlopen(self) -> None:
        os.environ["TOM_WECHAT_CREATE_IMAGE_TIMEOUT"] = "33"
        seen = []

        def fake(request, timeout):
            seen.append(timeout)
            return FakeResponse(200, json.dumps({"data": [{"b64_json": PNG_B64}]}).encode())

        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, 0, errs)
        self.assertEqual(seen, [33.0])


class GeminiTests(BackendTestCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ.update(
            TOM_WECHAT_IMAGE_BACKEND="gemini",
            TOM_WECHAT_IMAGE_MODEL="models/gemini-3-pro-image-preview",
            TOM_WECHAT_IMAGE_API_KEY=SECRET,
        )

    def test_request_shape_and_inline_decode(self) -> None:
        source = self.work / "ref.jpg"
        source.write_bytes(b"\xff\xd8\xff jpeg bytes")
        draft = base64.b64encode(b"draft").decode()
        response = {
            "candidates": [{"content": {"parts": [
                {"text": "thinking"},
                {"thought": True, "inlineData": {"mimeType": "image/png", "data": draft}},
                {"inlineData": {"mimeType": "image/png", "data": PNG_B64}},
            ]}}]
        }
        fake = FakeHttp((200, response))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, out, errs = self.run_main(
                "--prompt", "redraw", "--output", "g.png", "--input", str(source),
                "--aspect-ratio", "21:9", "--resolution", "2K",
            )
        self.assertEqual(code, 0, errs)
        request = fake.requests[0]
        self.assertEqual(
            request.full_url,
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent",
        )
        self.assertNotIn(SECRET, request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), SECRET)
        body = json.loads(request.data)
        parts = body["contents"][0]["parts"]
        self.assertEqual(parts[0]["inline_data"]["mime_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(parts[0]["inline_data"]["data"]), source.read_bytes())
        self.assertIn("redraw", parts[1]["text"])
        self.assertIn("21:9 aspect ratio", parts[1]["text"])
        config = body["generationConfig"]
        self.assertEqual(config["responseModalities"], ["TEXT", "IMAGE"])
        self.assertEqual(config["imageConfig"], {"aspectRatio": "21:9", "imageSize": "2K"})
        self.assertEqual((self.work / "images" / "g.png").read_bytes(), PNG)
        self.assertNotIn(SECRET, out + errs)

    def test_custom_base_url(self) -> None:
        os.environ["TOM_WECHAT_IMAGE_BASE_URL"] = "https://proxy.example.com/gemini/v1beta/"
        fake = FakeHttp((200, {"candidates": [{"content": {"parts": [{"inline_data": {"data": PNG_B64}}]}}]}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, errs = self.run_main("--prompt", "x", "--output", "g.png")
        self.assertEqual(code, 0, errs)
        self.assertTrue(fake.requests[0].full_url.startswith("https://proxy.example.com/gemini/v1beta/models/"))

    def test_image_size_rejection_retries_without_it(self) -> None:
        fake = FakeHttp(
            (400, {"error": {"message": "Unknown name \"imageSize\" at 'generation_config.image_config'"}}),
            (200, {"candidates": [{"content": {"parts": [{"inlineData": {"data": PNG_B64}}]}}]}),
        )
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, errs = self.run_main("--prompt", "x", "--output", "g.png", "--resolution", "4K")
        self.assertEqual(code, 0, errs)
        second = json.loads(fake.requests[1].data)
        self.assertNotIn("imageSize", second["generationConfig"]["imageConfig"])

    def test_blocked_prompt_exits_12(self) -> None:
        fake = FakeHttp((200, {"promptFeedback": {"blockReason": "SAFETY"}}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, errs = self.run_main("--prompt", "x", "--output", "g.png")
        self.assertEqual(code, image_backend.EXIT_NO_IMAGE)
        self.assertIn("SAFETY", errs)

    def test_text_only_answer_exits_12(self) -> None:
        fake = FakeHttp((200, {"candidates": [{"content": {"parts": [{"text": "I cannot draw that"}]}}]}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, errs = self.run_main("--prompt", "x", "--output", "g.png")
        self.assertEqual(code, image_backend.EXIT_NO_IMAGE)
        self.assertIn("I cannot draw that", errs)


class ExitCodeTests(BackendTestCase):
    def test_argument_errors_exit_2(self) -> None:
        cases = [
            ("--output", "a.png"),
            ("--prompt", "x"),
            ("--prompt", "x", "--output", "dir/a.png"),
            ("--prompt", "x", "--output", "a.png", "--aspect-ratio", "5:4"),
            ("--prompt", "x", "--output", "a.png", "--resolution", "8K"),
            ("--unknown",),
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                errs = io.StringIO()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errs):
                    try:
                        code = image_backend.main(list(argv))
                    except SystemExit as exc:  # argparse rejects unknown flags this way
                        code = exc.code
                self.assertEqual(code, image_backend.EXIT_ARGS)
                self.assertIn("[image-backend:args]", errs.getvalue())

    def test_missing_input_exits_5(self) -> None:
        os.environ.update(TOM_WECHAT_IMAGE_BACKEND="openai", TOM_WECHAT_IMAGE_MODEL="m", TOM_WECHAT_IMAGE_API_KEY=SECRET)
        code, _out, _errs = self.run_main("--prompt", "x", "--output", "a.png", "--input", "nope.png")
        self.assertEqual(code, image_backend.EXIT_INPUT)

    def test_missing_model_or_key_exits_4(self) -> None:
        os.environ.update(TOM_WECHAT_IMAGE_BACKEND="openai", TOM_WECHAT_IMAGE_API_KEY=SECRET)
        code, _out, errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, image_backend.EXIT_ENV)
        self.assertIn("TOM_WECHAT_IMAGE_MODEL", errs)
        os.environ.pop("TOM_WECHAT_IMAGE_API_KEY")
        os.environ["TOM_WECHAT_IMAGE_MODEL"] = "m"
        code, _out, errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, image_backend.EXIT_ENV)
        self.assertIn("no API key", errs)

    def test_plain_http_base_is_rejected_except_loopback(self) -> None:
        os.environ.update(TOM_WECHAT_IMAGE_BACKEND="openai", TOM_WECHAT_IMAGE_MODEL="m", TOM_WECHAT_IMAGE_API_KEY=SECRET)
        os.environ["TOM_WECHAT_IMAGE_BASE_URL"] = "http://api.example.com/v1"
        code, _out, _errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, image_backend.EXIT_ENV)
        os.environ["TOM_WECHAT_IMAGE_BASE_URL"] = "http://127.0.0.1:8080/v1"
        fake = FakeHttp((200, {"data": [{"b64_json": PNG_B64}]}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, 0, errs)

    def test_images_path_that_is_a_file_exits_13(self) -> None:
        (self.work / "images").write_text("not a dir", encoding="utf-8")
        os.environ.update(TOM_WECHAT_IMAGE_BACKEND="openai", TOM_WECHAT_IMAGE_MODEL="m", TOM_WECHAT_IMAGE_API_KEY=SECRET)
        fake = FakeHttp((200, {"data": [{"b64_json": PNG_B64}]}))
        with mock.patch.object(image_backend, "_urlopen", fake):
            code, _out, _errs = self.run_main("--prompt", "x", "--output", "a.png")
        self.assertEqual(code, image_backend.EXIT_WRITE)


FAKE_TOOL = """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["FAKE_LOG"]).write_text(json.dumps(args), encoding="utf-8")
print("tool chatter on stdout")
if os.environ.get("FAKE_WRITE", "1") == "1":
    Path(args[args.index("--out") + 1]).write_bytes(bytes.fromhex(os.environ["FAKE_PNG_HEX"]))
raise SystemExit(int(os.environ.get("FAKE_EXIT", "0")))
"""

FAKE_COMATE = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_ARGS"
printf '%s' "${COMATE_USERNAME_ENCRYPTED:-}" > "$FAKE_IDENTITY"
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in --output) out="$2"; shift 2 ;; *) shift ;; esac
done
code="${FAKE_EXIT:-0}"
if [[ "$code" == 0 ]]; then
  mkdir -p images && printf 'png' > "images/$out" && printf '%s\\n' "$PWD/images/$out"
fi
exit "$code"
"""

FAKE_SECURITY = """#!/bin/sh
if [ "$1" = find-generic-password ] && [ "$3" = tom-wechat-publisher-image ] && [ "$5" = api.example.com ]; then
  echo sk-from-keychain-999
  exit 0
fi
exit 44
"""


class CliTestCase(unittest.TestCase):
    """Runs image_backend.py as a subprocess, the way callers use it."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.work = self.tmp / "work"
        self.bin = self.tmp / "bin"
        self.home = self.tmp / "home"
        for path in (self.work, self.bin, self.home):
            path.mkdir()
        self.log = self.tmp / "tool-args.json"
        self.tool = self.tmp / "fake_tool.py"
        self.tool.write_text(FAKE_TOOL, encoding="utf-8")
        # PATH holds only fakes, so the real Keychain (`security`) is never reached.
        self.env = clean_env(HOME=str(self.home), PATH=str(self.bin), FAKE_LOG=str(self.log), FAKE_PNG_HEX=PNG.hex())

    def cli(self, *argv: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BACKEND), *argv],
            cwd=self.work,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=60,
        )

    def command_template(self) -> str:
        return (
            f"{sys.executable} {self.tool} --prompt {{prompt}} --out {{output}} "
            "--image={input} --ar {aspect} --res {resolution}"
        )

    def install(self, name: str, content: str) -> Path:
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path


class CommandBackendTests(CliTestCase):
    def test_template_substitution_without_input(self) -> None:
        result = self.cli(
            "--prompt", "draw {output} literally; it's fine", "--output", "c.png", "--aspect-ratio", "4:3",
            TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND=self.command_template(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        target = (self.work / "images" / "c.png").resolve()
        self.assertEqual(result.stdout.strip(), str(target))
        self.assertEqual(target.read_bytes(), PNG)
        args = json.loads(self.log.read_text(encoding="utf-8"))
        self.assertEqual(
            args,
            ["--prompt", "draw {output} literally; it's fine", "--out", str(target), "--ar", "4:3", "--res", "1K"],
        )
        self.assertIn("tool chatter", result.stderr)

    def test_template_with_input(self) -> None:
        ref = self.work / "ref.png"
        ref.write_bytes(PNG)
        result = self.cli(
            "--prompt", "p", "--output", "c.png", "--input", "ref.png", "--resolution", "2K",
            TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND=self.command_template(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text(encoding="utf-8"))
        self.assertIn(f"--image={ref.resolve()}", args)
        self.assertEqual(args[args.index("--res") + 1], "2K")

    def test_command_failure_code_passes_through(self) -> None:
        result = self.cli(
            "--prompt", "p", "--output", "c.png",
            TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND=self.command_template(), FAKE_EXIT="7",
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "")

    def test_success_without_output_exits_13(self) -> None:
        stale = self.work / "images" / "c.png"
        stale.parent.mkdir()
        stale.write_bytes(b"old")
        result = self.cli(
            "--prompt", "p", "--output", "c.png",
            TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND=self.command_template(), FAKE_WRITE="0",
        )
        self.assertEqual(result.returncode, image_backend.EXIT_WRITE)
        self.assertFalse(stale.exists(), "stale output must not pass for a fresh image")

    def test_missing_binary_exits_3(self) -> None:
        result = self.cli(
            "--prompt", "p", "--output", "c.png",
            TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND="no-such-image-tool {prompt} {output}",
        )
        self.assertEqual(result.returncode, image_backend.EXIT_DEPS)
        self.assertIn("command not found", result.stderr)

    def test_unit_argv_drops_input_tokens(self) -> None:
        values = {"prompt": "a b", "output": "/o.png", "input": "", "aspect": "1:1", "resolution": "1K"}
        argv = image_backend.command_argv("tool -p {prompt} --ref {input} --ref2={input} -o {output}", values, False)
        self.assertEqual(argv, ["tool", "-p", "a b", "-o", "/o.png"])
        values["input"] = "/in.png"
        argv = image_backend.command_argv("tool --ref {input} -o {output}", values, True)
        self.assertEqual(argv, ["tool", "--ref", "/in.png", "-o", "/o.png"])


class CheckTests(CliTestCase):
    def check(self, **env: str) -> tuple:
        result = self.cli("--check", "--json", **env)
        human = self.cli("--check", **env)
        self.assertEqual(result.returncode, human.returncode)
        return result.returncode, json.loads(result.stdout), human.stdout + result.stdout + result.stderr + human.stderr

    def test_comate_usable_with_script_and_identity(self) -> None:
        script = self.install("generate-image.sh", FAKE_COMATE)
        code, report, _text = self.check(TOM_WECHAT_CREATE_IMAGE_BASH=str(script), COMATE_USERNAME_ENCRYPTED="enc")
        self.assertEqual(code, 0)
        self.assertEqual(report["backend"], "comate")
        self.assertEqual(report["source"], "default")
        self.assertTrue(report["usable"])
        self.assertEqual(report["details"], {"script": str(script), "identity": "present"})

    def test_comate_missing_script_is_unusable(self) -> None:
        code, report, text = self.check(COMATE_USERNAME_ENCRYPTED="enc")
        self.assertEqual(code, image_backend.EXIT_ENV)
        self.assertFalse(report["usable"])
        self.assertIn("create-image wrapper not found", " ".join(report["problems"]))
        self.assertIn("usable: no", text)

    def test_openai_reports_missing_pieces(self) -> None:
        code, report, _text = self.check(TOM_WECHAT_IMAGE_BACKEND="openai")
        self.assertEqual(code, image_backend.EXIT_ENV)
        self.assertEqual(report["details"]["base_url"], "https://api.openai.com/v1")
        self.assertEqual(report["details"]["key"], "missing")
        problems = " ".join(report["problems"])
        self.assertIn("TOM_WECHAT_IMAGE_MODEL", problems)
        self.assertIn("account=api.openai.com", problems)

    def test_openai_env_key_is_never_printed(self) -> None:
        code, report, text = self.check(TOM_WECHAT_IMAGE_BACKEND="openai", TOM_WECHAT_IMAGE_MODEL="gpt-image-1", TOM_WECHAT_IMAGE_API_KEY=SECRET)
        self.assertEqual(code, 0, text)
        self.assertEqual(report["details"]["key"], "present (env TOM_WECHAT_IMAGE_API_KEY)")
        self.assertNotIn(SECRET, text)

    def test_gemini_key_from_keychain(self) -> None:
        self.install("security", FAKE_SECURITY)
        code, report, text = self.check(
            TOM_WECHAT_IMAGE_BACKEND="gemini",
            TOM_WECHAT_IMAGE_MODEL="gemini-2.5-flash-image",
            TOM_WECHAT_IMAGE_BASE_URL="https://api.example.com/v1beta",
        )
        self.assertEqual(code, 0, text)
        self.assertEqual(report["details"]["key"], "present (keychain tom-wechat-publisher-image/api.example.com)")
        self.assertNotIn("sk-from-keychain-999", text)

    def test_command_check(self) -> None:
        code, report, _text = self.check(TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND=self.command_template())
        self.assertEqual(code, 0)
        self.assertEqual(report["details"]["binary"], sys.executable)
        code, report, _text = self.check(TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND="no-such-tool {output}")
        self.assertEqual(code, image_backend.EXIT_ENV)
        self.assertIn("command not found: no-such-tool", report["problems"])
        code, report, _text = self.check(TOM_WECHAT_IMAGE_BACKEND="command")
        self.assertIn("TOM_WECHAT_IMAGE_COMMAND is not set", report["problems"])

    def test_profile_selection_shows_in_check(self) -> None:
        code, report, _text = self.check(TOM_WECHAT_ACCOUNT="medical", COMATE_USERNAME_ENCRYPTED="enc")
        self.assertEqual(report["backend"], "comate")
        code, report, _text = self.check(TOM_WECHAT_IMAGE_BACKEND="bogus")
        self.assertEqual(code, image_backend.EXIT_ENV)
        self.assertIsNone(report["backend"])
        self.assertIn("unknown image backend", report["problems"][0])


class ComateBackendTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.script = self.tmp / "generate-image.sh"
        self.script.write_text(FAKE_COMATE, encoding="utf-8")
        self.script.chmod(0o755)
        self.args_file = self.tmp / "comate-args.txt"
        self.identity_file = self.tmp / "comate-identity.txt"
        self.env.update(
            PATH="/bin:/usr/bin",
            TOM_WECHAT_CREATE_IMAGE_BASH=str(self.script),
            FAKE_ARGS=str(self.args_file),
            FAKE_IDENTITY=str(self.identity_file),
        )

    def test_args_pass_through_unchanged(self) -> None:
        ref = self.work / "ref.png"
        ref.write_bytes(PNG)
        result = self.cli(
            "--prompt", "p with spaces", "--output", "x.png", "--input", str(ref),
            "--resolution", "2K", "--aspect-ratio", "16:9", COMATE_USERNAME_ENCRYPTED="enc-env",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.args_file.read_text(encoding="utf-8").splitlines(),
            ["--prompt", "p with spaces", "--output", "x.png", "--input", str(ref), "--resolution", "2K", "--aspect-ratio", "16:9"],
        )
        self.assertEqual(self.identity_file.read_text(encoding="utf-8"), "enc-env")
        self.assertTrue(result.stdout.strip().endswith("images/x.png"))

    def test_identity_is_derived_from_login_token(self) -> None:
        login = self.home / ".comate" / "login-user"
        login.mkdir(parents=True)
        payload = base64.urlsafe_b64encode(json.dumps({"content": {"identity": "enc-from-token"}}).encode()).decode().rstrip("=")
        (login / "tom").write_text(f"Bearer-hdr.{payload}.sig", encoding="utf-8")
        result = self.cli("--prompt", "p", "--output", "x.png")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.identity_file.read_text(encoding="utf-8"), "enc-from-token")
        self.assertNotIn("enc-from-token", result.stdout + result.stderr)

    def test_script_exit_code_passes_through(self) -> None:
        result = self.cli("--prompt", "p", "--output", "x.png", COMATE_USERNAME_ENCRYPTED="e", FAKE_EXIT="11")
        self.assertEqual(result.returncode, 11)

    def test_missing_script_exits_3(self) -> None:
        result = self.cli("--prompt", "p", "--output", "x.png", TOM_WECHAT_CREATE_IMAGE_BASH=str(self.tmp / "missing.sh"))
        self.assertEqual(result.returncode, image_backend.EXIT_DEPS)
        self.assertIn("TOM_WECHAT_IMAGE_BACKEND", result.stderr)


class GenerateArticleIntegrationTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        import generate_article  # noqa: E402

        self.ga = generate_article
        env_patch = mock.patch.dict(os.environ, self.env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)

    def test_run_create_image_uses_selected_backend(self) -> None:
        os.environ.update(TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND=self.command_template())
        with contextlib.redirect_stderr(io.StringIO()):
            ok = self.ga.run_create_image("a prompt", "slot.png", aspect="21:9", resolution="2K")
        self.assertTrue(ok)
        self.assertEqual((self.work / "images" / "slot.png").read_bytes(), PNG)
        args = json.loads(self.log.read_text(encoding="utf-8"))
        self.assertEqual(args[args.index("--ar") + 1], "21:9")

    def test_config_errors_are_not_retried(self) -> None:
        os.environ.update(TOM_WECHAT_IMAGE_BACKEND="openai")
        errs = io.StringIO()
        with mock.patch.object(self.ga, "CREATE_IMAGE_ATTEMPTS", 3), contextlib.redirect_stderr(errs):
            ok = self.ga.run_create_image("a prompt", "slot.png")
        self.assertFalse(ok)
        self.assertIn("attempt 1/3 failed", errs.getvalue())
        self.assertNotIn("attempt 2/3", errs.getvalue())

    def test_status_comes_from_check(self) -> None:
        os.environ.update(TOM_WECHAT_IMAGE_BACKEND="command", TOM_WECHAT_IMAGE_COMMAND=self.command_template())
        status = self.ga.image_backend_status()
        self.assertTrue(status["usable"])
        self.assertEqual(status["backend"], "command")
        os.environ["TOM_WECHAT_IMAGE_BACKEND"] = "gemini"
        self.assertFalse(self.ga.image_backend_status()["usable"])


if __name__ == "__main__":
    unittest.main()

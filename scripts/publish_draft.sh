#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  publish_draft.sh article.md [--title "Title"] [--cover cover.jpg] [--author "Name"] [--theme theme] [--highlight theme] [--preview-themes] [--preview-dir DIR] [--no-open-preview] [--no-footnote] [--render-only] [--dry-run] [--keep-temp] [--theme-selection-confirmed] [--require-body-images] [--upload]
  publish_draft.sh --generate-from SOURCE --topic "..." [--out-dir DIR] [--history-root DIR] [--topic-history-reviewed] [--allow-topic-repeat] [--search-query "..."] [--image-visual-theme THEME] [--image-theme-selection-confirmed] [--no-search] [--no-images] [--markdown] [--humanize]
  publish_draft.sh --humanize article.md [--in-place] [--score] [--json]

Generate mode (--generate-from):
  SOURCE  URL or local path of the source article to summarize/illustrate.
  Pulls supplementary web facts, composes a short WeChat Markdown (5-min read,
  image-heavy), generates illustrations via the /create-image workflow,
  then exits. If image generation fails, fix the prompt/article/backend and rerun;
  this wrapper does not switch to any alternate image backend.

Humanize mode (--humanize):
  Strips AI-writing tells (24-pattern catalog from op7418/Humanizer-zh +
  blader/humanizer + hardikpandya/stop-slop) from a WeChat Markdown article.
  Runs deterministic rewrites and scores the result on the 5-dim / 50-point
  Humanizer-zh rubric. Use --in-place to overwrite, --score to inspect only.
  --humanize is also applied automatically at the end of --generate-from
  unless --no-humanize is given.

Environment:
  Wenyan owns WeChat credentials and account selection. Configure them with
  `wenyan credential` or   WECHAT_APP_ID / WECHAT_APP_SECRET. WECHAT_APP_ID is required for uploads.
  WECHAT_AUTHOR       optional author override; defaults to 赵涛
  WECHAT_THEME        optional default theme; defaults to dns-daily
  WECHAT_HIGHLIGHT    optional default code highlight theme
  TOM_WECHAT_IMAGE_VISUAL_THEME  required for illustrated generate mode unless --no-images
  COMATE_USERNAME_ENCRYPTED      optional for /create-image; auto-derived from ~/.comate/login-user when missing
  TOM_WECHAT_CREATE_IMAGE_BASH   override path to the create-image wrapper
  TOM_WECHAT_CREATE_IMAGE_PS1    override path to the create-image PowerShell wrapper
  TOM_WECHAT_PYTHON              optional Python executable override; Python 3.9+ is required
  TOM_WECHAT_CHECK_PROSE         optional human-writing prose checker override
  TOM_WECHAT_HISTORY_ROOT        historical article root; defaults to ~/Desktop/wechat
  TOM_WECHAT_FAILURE_NOTIFY      optional 如流 recipient for failure alerts; disabled by default

This wrapper prepares WeChat-compatible Markdown/images and renders with Wenyan by default.
Draft creation requires explicit --upload. It never publishes or mass-sends.
By default, --preview-themes opens generated HTML previews; use --no-open-preview to skip this.
USAGE
}

mask_app_id() {
  local value="${1:-}"
  if [[ ${#value} -le 8 ]]; then
    printf '***\n'
  else
    printf '%s...%s\n' "${value:0:4}" "${value: -4}"
  fi
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_dir="$(cd "$script_dir/.." && pwd)"
# Set WECHAT_APP_ID explicitly for draft uploads; public builds must not bind to a personal account.

# ---- failure notification (如流) -------------------------------------------
# On any non-zero exit during actual work (generate / humanize / prepare /
# preview / upload), send a best-effort 如流 single-chat message to the
# configured recipient so silent background failures surface immediately.
#   * Opt in by setting TOM_WECHAT_FAILURE_NOTIFY to a recipient.
#   * Best-effort: if infoflow-message-group is missing or unconfigured, the
#     trap logs a notice and still exits with the original status.
wechat_failure_err_log="$(mktemp "${TMPDIR:-/tmp}/wechat-draft.err.XXXXXX")"
wechat_failure_context=""

send_infoflow_failure() {
  local exit_code=$?
  if [[ $# -gt 0 ]]; then
    exit_code="$1"
  fi
  if [[ "$exit_code" -eq 0 || -z "$wechat_failure_context" ]]; then
    rm -f "$wechat_failure_err_log" 2>/dev/null || true
    return 0
  fi
  local recipient="${TOM_WECHAT_FAILURE_NOTIFY:-}"
  local infoflow_dir="$HOME/.comate/skills/infoflow-message-group"
  local err_tail=""
  if [[ -s "$wechat_failure_err_log" ]]; then
    err_tail="$(tail -n 10 "$wechat_failure_err_log" | tr -d '\\' | tr '\r\n' '  ' | sed 's/  */ /g' | cut -c1-500)"
  fi
  local msg
  msg="⚠️ tom-wechat-publisher 任务失败\n退出码: ${exit_code}\n上下文: ${wechat_failure_context}\n错误尾部: ${err_tail:-无 stderr 输出}"
  if [[ -z "$recipient" || ! -f "$infoflow_dir/scripts/msg_send_single.sh" ]]; then
    printf '[notify] 如流通知跳过 (recipient=%s, infoflow=%s)\n' \
      "${recipient:-<empty>}" "$([ -f "$infoflow_dir/scripts/msg_send_single.sh" ] && echo ok || echo missing)" >&2
    rm -f "$wechat_failure_err_log" 2>/dev/null || true
    return 0
  fi
  (
    cd "$infoflow_dir" || exit 0
    eval "$(bash ./scripts/setup.sh 2>/dev/null)" 2>/dev/null || true
    [[ -z "${INFOFLOW_TOKEN:-}" ]] && exit 0
    bash ./scripts/msg_send_single.sh "$recipient" md "$msg" >/dev/null 2>&1 || true
  ) || true
  printf '[notify] 已发送如流失败通知给 %s (退出码 %s)\n' "$recipient" "$exit_code" >&2
  rm -f "$wechat_failure_err_log" 2>/dev/null || true
  return 0
}
trap send_infoflow_failure EXIT
# Mirror all stderr to the failure log so the trap can include the last error.
exec 2> >(tee "$wechat_failure_err_log" >&2)



if [[ $# -eq 0 ]]; then
  usage
  exit 2
fi

article=""
title=""
cover=""
author="${WECHAT_AUTHOR:-赵涛}"
theme="${WECHAT_THEME:-dns-daily}"
theme_explicit=0
highlight="${WECHAT_HIGHLIGHT:-solarized-light}"
render_only=0
preview_themes=0
open_preview=1
dry_run=0
keep_temp=0
theme_selection_confirmed=0
require_body_images=0
upload=0
persistent_preview_dir=""
footnote=1

# Generate-mode state
generate_from=""
generate_topic=""
generate_out_dir=""
generate_search_query=""
generate_no_search=0
generate_no_images=0
generate_markdown=0
generate_humanize=1  # default ON at end of --generate-from
generate_history_root="${TOM_WECHAT_HISTORY_ROOT:-$HOME/Desktop/wechat}"
topic_history_reviewed=0
allow_topic_repeat=0
image_visual_theme="${TOM_WECHAT_IMAGE_VISUAL_THEME:-}"
image_theme_selection_confirmed=0

# Humanize-mode state
humanize_path=""
humanize_in_place=0
humanize_score=0
humanize_json=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --title)
      title="${2:-}"
      shift 2
      ;;
    --cover)
      cover="${2:-}"
      shift 2
      ;;
    --author)
      author="${2:-}"
      shift 2
      ;;
    --theme)
      theme="${2:-}"
      theme_explicit=1
      shift 2
      ;;
    --highlight)
      highlight="${2:-}"
      shift 2
      ;;
    --preview-themes)
      render_only=1
      preview_themes=1
      keep_temp=1
      shift
      ;;
    --no-open-preview)
      open_preview=0
      shift
      ;;
    --no-footnote)
      footnote=0
      shift
      ;;
    --preview-dir)
      persistent_preview_dir="${2:-}"
      if [[ -z "$persistent_preview_dir" ]]; then
        echo "ERROR: --preview-dir requires a directory" >&2
        exit 2
      fi
      shift 2
      ;;
    --render-only)
      render_only=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --upload)
      upload=1
      shift
      ;;
    --upload-command)
      echo "ERROR: --upload-command is no longer supported; Wenyan is the built-in draft uploader." >&2
      exit 2
      ;;
    --keep-temp)
      keep_temp=1
      shift
      ;;
    --theme-selection-confirmed)
      theme_selection_confirmed=1
      shift
      ;;
    --require-body-images)
      require_body_images=1
      shift
      ;;
    --image-visual-theme)
      image_visual_theme="${2:-}"
      shift 2
      ;;
    --image-theme-selection-confirmed)
      image_theme_selection_confirmed=1
      shift
      ;;
    --generate-from)
      generate_from="${2:-}"
      shift 2
      ;;
    --topic)
      generate_topic="${2:-}"
      shift 2
      ;;
    --history-root)
      generate_history_root="${2:-}"
      shift 2
      ;;
    --topic-history-reviewed)
      topic_history_reviewed=1
      shift
      ;;
    --allow-topic-repeat)
      allow_topic_repeat=1
      shift
      ;;
    --out-dir)
      generate_out_dir="${2:-}"
      shift 2
      ;;
    --search-query)
      generate_search_query="${2:-}"
      shift 2
      ;;
    --no-search)
      generate_no_search=1
      shift
      ;;
    --no-images)
      generate_no_images=1
      shift
      ;;
    --markdown)
      generate_markdown=1
      shift
      ;;
    --humanize)
      generate_humanize=1
      shift
      ;;
    --no-humanize)
      generate_humanize=0
      shift
      ;;
    --in-place)
      humanize_in_place=1
      shift
      ;;
    --score)
      humanize_score=1
      shift
      ;;
    --json)
      humanize_json=1
      shift
      ;;
    --*)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$article" && -z "$humanize_path" ]]; then
        humanize_path="$1"
      elif [[ -n "$article" ]]; then
        echo "ERROR: multiple article files provided: $article and $1" >&2
        exit 2
      else
        article="$1"
      fi
      shift
      ;;
  esac
done

resolve_python() {
  local requested="${TOM_WECHAT_PYTHON:-python3}"
  local executable=""
  if [[ "$requested" == */* ]]; then
    [[ -x "$requested" ]] && executable="$requested"
  else
    executable="$(command -v "$requested" 2>/dev/null || true)"
  fi
  if [[ -z "$executable" ]]; then
    echo "ERROR: Python executable not found: $requested" >&2
    return 127
  fi
  if ! "$executable" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "ERROR: Python 3.9+ is required: $executable" >&2
    return 2
  fi
  printf '%s\n' "$executable"
}

resolve_check_prose() {
  local candidate
  local candidates=()
  if [[ -n "${TOM_WECHAT_CHECK_PROSE:-}" ]]; then
    candidates+=("$TOM_WECHAT_CHECK_PROSE")
  fi
  candidates+=(
    "$HOME/.codex/skills/human-writing/scripts/check_prose.py"
    "$HOME/.agents/skills/human-writing/scripts/check_prose.py"
    "$HOME/.comate/skills/human-writing/scripts/check_prose.py"
  )
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON="$(resolve_python)"

# ---- humanize mode ---------------------------------------------------------
if [[ -n "$humanize_path" ]]; then
  humanize_cmd=("$PYTHON" "$script_dir/humanize_article.py" "$humanize_path")
  if [[ "$humanize_in_place" -eq 1 ]]; then
    humanize_cmd+=(--in-place)
  fi
  if [[ "$humanize_score" -eq 1 ]]; then
    humanize_cmd+=(--score)
  fi
  if [[ "$humanize_json" -eq 1 ]]; then
    humanize_cmd+=(--json)
  fi
  wechat_failure_context="humanize: ${humanize_path}"
  "${humanize_cmd[@]}"
  exit 0
fi

# ---- generate mode ---------------------------------------------------------
if [[ -n "$generate_from" ]]; then
  if [[ -z "$generate_out_dir" ]]; then
    echo "ERROR: --generate-from requires --out-dir" >&2
    exit 2
  fi
  if [[ -z "$generate_topic" ]]; then
    echo "ERROR: --generate-from requires --topic so historical topic reuse can be checked before writing" >&2
    exit 2
  fi
  if [[ -z "$generate_history_root" || ! -d "$generate_history_root" ]]; then
    echo "ERROR: topic history root is not a directory: ${generate_history_root:-<empty>}" >&2
    exit 2
  fi
  date_prefix="$(date +%Y-%m%d)"
  out_parent="$(dirname "$generate_out_dir")"
  out_base="$(basename "$generate_out_dir")"
  if [[ ! "$out_base" =~ ^[0-9]{4}-[0-9]{4}- ]]; then
    generate_out_dir="$out_parent/$date_prefix-$out_base"
  fi
  history_cmd=("$PYTHON" "$script_dir/check_topic_history.py"
    "--topic" "$generate_topic"
    "--source" "$generate_from"
    "--history-root" "$generate_history_root"
    "--exclude" "$generate_out_dir"
  )
  printf 'Checking topic against history: %s\n' "$generate_history_root" >&2
  set +e
  "${history_cmd[@]}"
  history_status=$?
  set -e
  case "$history_status" in
    0)
      ;;
    3)
      if [[ "$allow_topic_repeat" -ne 1 ]]; then
        echo "ERROR: duplicate topic detected; stop before writing or rerun only after the user explicitly requests an update/sequel with --allow-topic-repeat" >&2
        exit 3
      fi
      printf 'Topic repetition explicitly allowed for an update/sequel.\n' >&2
      ;;
    4)
      if [[ "$topic_history_reviewed" -ne 1 && "$allow_topic_repeat" -ne 1 ]]; then
        echo "ERROR: related historical articles require angle review; state the distinct angle, then rerun with --topic-history-reviewed" >&2
        exit 4
      fi
      printf 'Related-topic history review confirmed.\n' >&2
      ;;
    *)
      echo "ERROR: topic history check failed with exit code $history_status" >&2
      exit "$history_status"
      ;;
  esac
  gen_cmd=("$PYTHON" "$script_dir/generate_article.py"
    "--source" "$generate_from"
    "--out-dir" "$generate_out_dir"
    "--topic" "$generate_topic"
    "--search-query" "$generate_search_query"
  )
  if [[ "$generate_no_search" -eq 1 ]]; then
    gen_cmd+=("--no-search")
  fi
  if [[ "$generate_no_images" -eq 1 ]]; then
    gen_cmd+=("--no-images")
  else
    if [[ -z "$image_visual_theme" || "$image_theme_selection_confirmed" -ne 1 ]]; then
      cat >&2 <<'THEMES'
ERROR: illustrated generate mode requires an explicit image style for this article.
Choose one and rerun with --image-visual-theme STYLE --image-theme-selection-confirmed.
Available image styles:
  - clean-professional: 极简专业, Apple product page meets Monocle editorial
  - editorial-magazine: 编辑杂志, Kinfolk meets Wired feature spread
  - snoopy-comic: Snoopy 温暖漫画, Peanuts-like warm comic feeling
  - bold-statement: 大字报, brutalist typography-led editorial statement
  - data-infographic: 数据信息图, The Economist meets Information is Beautiful
  - whiteboard-sketch: 手绘白板, xkcd meets professor's whiteboard
  - warm-narrative: 温暖叙事, Airbnb/Mailchimp-like approachable warmth
The image style is separate from the later article layout theme selected after --preview-themes.
THEMES
      exit 2
    fi
    gen_cmd+=("--image-visual-theme" "$image_visual_theme")
  fi
  if [[ "$generate_markdown" -eq 1 ]]; then
    gen_cmd+=("--markdown")
  fi
  printf 'Generating article from: %s\n' "$generate_from" >&2
  printf 'Output directory: %s\n' "$generate_out_dir" >&2
  wechat_failure_context="generate-from: ${generate_from} | topic: ${generate_topic} | out: ${generate_out_dir}"
  "${gen_cmd[@]}"

  if [[ "$generate_humanize" -eq 1 ]]; then
    generated_md="$generate_out_dir/article.md"
    if [[ -f "$generated_md" ]]; then
      printf '\n[humanize] stripping AI tells from %s ...\n' "$generated_md" >&2
      wechat_failure_context="humanize: ${generated_md} (after generate-from: ${generate_from})"
      "$PYTHON" "$script_dir/humanize_article.py" "$generated_md" --in-place
    fi
  fi

  printf 'Next step: review %s/article.md, then run:\n' "$generate_out_dir" >&2
  printf '  bash %q %q --preview-themes\n' "$0" "$generate_out_dir/article.md"
  exit 0
fi

if [[ -z "$article" ]]; then
  echo "ERROR: article markdown file is required" >&2
  exit 2
fi

if [[ ! -f "$article" ]]; then
  echo "ERROR: article file not found: $article" >&2
  exit 2
fi

if ! command -v wenyan >/dev/null 2>&1; then
  echo "ERROR: wenyan CLI is required. Install @wenyan-md/cli before continuing." >&2
  exit 2
fi


if ! command -v sips >/dev/null 2>&1; then
  echo "ERROR: sips command not found; it is required to convert unsupported local images to PNG" >&2
  exit 127
fi

if [[ "$require_body_images" -eq 1 ]]; then
  article_dir="$(cd "$(dirname "$article")" && pwd)"
  image_specs="$article_dir/image_specs.json"
  if [[ ! -f "$image_specs" ]]; then
    echo "ERROR: --require-body-images needs $image_specs; run image_spec.py after the article is finalized." >&2
    exit 2
  fi
  wechat_failure_context="body-image-preflight: ${article}"
  "$PYTHON" "$script_dir/validate_body_images.py" "$article" --spec "$image_specs" --min 3 >&2
fi

if ! check_prose_script="$(resolve_check_prose)"; then
  echo "ERROR: prose preflight checker not found; set TOM_WECHAT_CHECK_PROSE." >&2
  exit 2
fi
wechat_failure_context="prose preflight: ${article}"
"$PYTHON" "$check_prose_script" "$article" >&2

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/wechat-draft.XXXXXX")"
cleanup() {
  status=$?
  trap - EXIT
  if [[ "$keep_temp" -eq 0 && -d "$work_dir" ]]; then
    rm -rf "$work_dir"
  elif [[ "$keep_temp" -eq 1 ]]; then
    printf 'Prepared upload workspace kept at: %s\n' "$work_dir" >&2
  fi
  if [[ "$status" -ne 0 ]]; then
    send_infoflow_failure "$status"
  fi
  exit "$status"
}
trap cleanup EXIT

prepared_file="$work_dir/article.md"
theme_file="$skill_dir/assets/$theme.css"
if [[ ! -f "$theme_file" ]]; then
  echo "ERROR: theme not found: $theme" >&2
  exit 2
fi
prepare_cmd=("$PYTHON" "$script_dir/prepare_wechat_md.py" "$article" --output "$prepared_file")
if [[ -n "$title" ]]; then
  prepare_cmd+=(--title "$title")
fi
if [[ -n "$cover" ]]; then
  prepare_cmd+=(--cover "$cover")
fi
if [[ -n "$author" ]]; then
  prepare_cmd+=(--author "$author")
fi

wechat_failure_context="prepare-wechat-md: ${article}"
"${prepare_cmd[@]}" >&2

wechat_failure_context="article-asset-preflight: ${article}"
"$PYTHON" "$script_dir/validate_article_assets.py" "$prepared_file" >&2

if [[ "$preview_themes" -eq 1 ]]; then
  preview_dir="$work_dir"
  preview_theme_output="$(TOM_WECHAT_PREVIEW_THEMES="${TOM_WECHAT_PREVIEW_THEMES:-}" TOM_WECHAT_PREVIEW_THEME_COUNT="${TOM_WECHAT_PREVIEW_THEME_COUNT:-5}" "$PYTHON" - <<'PY'
import os

fallback_themes = ["dns-elegant-purple", "dns-aurora-cyan", "dns-warm-orange", "dns-ocean", "dns-sakura"]
themes = fallback_themes

except Exception:
    themes = fallback_themes

preferred_themes = [
    # More expressive editorial themes first. The older simple themes remain
    # available for manual --theme selection, but are no longer the default
    # preview set because they make every article look the same.
    "dns-elegant-purple",
    "dns-aurora-cyan",
    "dns-warm-orange",
    "dns-ocean",
    "dns-sakura",
    "dns-cyber-lime",
    "dns-graphite",
    "dns-neo-green",
    "dns-tech-blue",
    "dns-ink-minimal",
    "dns-daily",
]
configured = [theme.strip() for theme in os.environ.get("TOM_WECHAT_PREVIEW_THEMES", "").replace(",", " ").split() if theme.strip()]
try:
    count = int(os.environ.get("TOM_WECHAT_PREVIEW_THEME_COUNT", "5"))
except ValueError:
    count = 5
count = min(8, max(3, count))

selected = [theme for theme in configured if theme in themes]
registered_preferred = [theme for theme in preferred_themes if theme in themes and theme not in selected]
selected.extend(registered_preferred[: max(0, count - len(selected))])
if len(selected) < count:
    selected.extend(theme for theme in themes if theme not in selected)
selected = selected[: min(count, len(selected))]
print(" ".join(selected))
PY
  )"
  read -r -a preview_theme_ids <<< "$preview_theme_output"
  printf 'Preview themes: %s\n' "${preview_theme_ids[*]}"
  preview_outputs=()
  for preview_theme in "${preview_theme_ids[@]}"; do
    preview_output="$preview_dir/article.${preview_theme}.html"
    "$PYTHON" "$script_dir/render_article_preview.py" --article "$prepared_file" --output "$preview_output" --theme "$preview_theme"
    preview_outputs+=("$preview_output")
    printf 'Preview generated [%s]: %s\n' "$preview_theme" "$preview_output"
  done
  if [[ "$open_preview" -eq 1 ]]; then
    if command -v open >/dev/null 2>&1; then
      printf 'Opening previews in browser for theme selection...\n'
      open "${preview_outputs[@]}"
    else
      printf 'Preview auto-open skipped: macOS open command not found.\n' >&2
    fi
  fi
  printf 'Review the previews, then upload with: %q %q --theme THEME --theme-selection-confirmed\n' "$0" "$article"
  exit 0
fi

preview_file="$work_dir/preview.html"
render_cmd=(wenyan render --file "$prepared_file" --custom-theme "$theme_file" --highlight "$highlight")
if [[ "$footnote" -eq 0 ]]; then
  render_cmd+=(--no-footnote)
fi
"${render_cmd[@]}" >"$preview_file"
printf 'Prepared markdown: %s\n' "$prepared_file"
printf 'Preview: %s\n' "$preview_file"
open_preview_file="$preview_file"
if [[ -n "$persistent_preview_dir" ]]; then
  persistent_preview_file="$("$PYTHON" "$script_dir/persist_preview.py" "$preview_file" "$work_dir" "$persistent_preview_dir")"
  open_preview_file="$persistent_preview_file"
  printf 'Persistent preview: %s\n' "$persistent_preview_file"
fi
printf 'Renderer: wenyan %s\n' "$(wenyan --version)"
printf 'Theme: %s\n' "$theme"

if [[ "$open_preview" -eq 1 && "$dry_run" -eq 0 ]] && command -v open >/dev/null 2>&1; then
  open "$open_preview_file"
fi
if [[ "$dry_run" -eq 1 || "$render_only" -eq 1 || "$upload" -eq 0 ]]; then
  [[ "$upload" -eq 0 ]] && printf 'Upload: skipped (explicit --upload not provided)\n'
  exit 0
fi
wechat_app_id="${WECHAT_APP_ID:-}"
wechat_app_secret="${WECHAT_APP_SECRET:-}"
if [[ -z "$wechat_app_secret" ]]; then
  if ! command -v security >/dev/null 2>&1; then
    echo "ERROR: WECHAT_APP_SECRET is unset and macOS Keychain is unavailable." >&2
    exit 2
  fi
  wechat_app_secret="$(security find-generic-password -s tom-wechat-publisher -a "$wechat_app_id" -w 2>/dev/null || true)"
fi
if [[ -z "$wechat_app_id" || -z "$wechat_app_secret" ]]; then
  echo "ERROR: WeChat AppID/AppSecret not found in environment or Keychain." >&2
  exit 2
fi
masked_wechat_app_id="$(mask_app_id "$wechat_app_id")"
printf 'Target WeChat AppID: %s\n' "$masked_wechat_app_id"
upload_cmd=(env WECHAT_APP_ID="$wechat_app_id" WECHAT_APP_SECRET="$wechat_app_secret" wenyan publish --file "$prepared_file" --custom-theme "$theme_file" --highlight "$highlight")
if [[ "$footnote" -eq 0 ]]; then
  upload_cmd+=(--no-footnote)
fi
upload_log="$work_dir/upload.log"
set +e
"${upload_cmd[@]}" 2>&1 | WECHAT_APP_ID="$wechat_app_id" WECHAT_APP_SECRET="$wechat_app_secret" MASKED_WECHAT_APP_ID="$masked_wechat_app_id" "$PYTHON" -c '
import os
import sys

secret = os.environ["WECHAT_APP_SECRET"].encode("utf-8")
app_id = os.environ["WECHAT_APP_ID"].encode("utf-8")
masked_app_id = os.environ["MASKED_WECHAT_APP_ID"].encode("utf-8")
output = sys.stdin.buffer.read()
output = output.replace(secret, b"[REDACTED]")
output = output.replace(app_id, masked_app_id)
sys.stdout.buffer.write(output)
' | tee "$upload_log"
upload_statuses=("${PIPESTATUS[@]}")
set -e
upload_status="${upload_statuses[0]}"
if [[ "${upload_statuses[1]}" -ne 0 || "${upload_statuses[2]}" -ne 0 ]]; then
  echo "ERROR: failed to sanitize or record Wenyan draft upload output." >&2
  exit 1
fi
if [[ "$upload_status" -ne 0 ]]; then
  echo "ERROR: wenyan draft upload failed with exit code $upload_status." >&2
  exit "$upload_status"
fi
if grep -Eiq '(^|[^[:alnum:]_])(error|failed|failure)([^[:alnum:]_]|$)|errcode[^0-9-]*-?[1-9][0-9]*' "$upload_log"; then
  echo "ERROR: wenyan reported an upload error even though it exited successfully." >&2
  exit 1
fi

wechat_failure_context="draft creation evidence: ${article}"
if ! draft_media_id="$("$PYTHON" "$script_dir/validate_publish_output.py" "$upload_log")"; then
  echo "ERROR: draft creation evidence validation failed." >&2
  exit 1
fi
printf 'Draft Media ID: %s\n' "$draft_media_id"
printf 'Draft created successfully.\n'

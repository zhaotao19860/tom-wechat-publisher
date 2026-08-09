# tom-wechat-publisher

一个面向微信公众号内容全流程的 Skill：它可以从 URL、网页公告、本地素材、会议纪要、对话或主题摘要生成文章，也可以处理已有 Markdown，完成配图、校验、主题预览和草稿创建。

它覆盖两条入口：一条是“内容生成”入口，把外部来源或当前对话中的素材整理成文章；另一条是“文章准备”入口，把已有 Markdown 规范化为适合微信公众号的素材。两条入口最终都会经过事实核对、图片处理、主题预览，并在用户明确授权后通过 Wenyan 创建公众号草稿。

> 该 Skill 只创建草稿，不发布、不群发。`--upload` 是外部副作用开关，必须由用户明确请求时才使用。

## 目录

- [它解决什么问题](#它解决什么问题)
- [支持的输入](#支持的输入)
- [Skill 构成](#skill-构成)
- [工作原理](#工作原理)
- [安全边界](#安全边界)
- [安装](#安装)
- [凭据配置](#凭据配置)
- [快速开始](#快速开始)
- [完整用法](#完整用法)
- [图片工作流](#图片工作流)
- [默认作者](#默认作者)
- [故障排查](#故障排查)
- [开发与验证](#开发与验证)
- [已知限制](#已知限制)
- [许可证与责任](#许可证与责任)

## 它解决什么问题

微信公众号文章从 Markdown 到草稿箱，通常涉及多个容易出错的步骤：

1. frontmatter 中的标题、作者、封面和正文图片路径需要一致；
2. SVG、WebP、HEIC 等图片可能不能直接作为微信素材上传；
3. 封面和正文图片的用途不同，不能用同一种生成方式处理；
4. 版本号、命令、配置项和表格数字不能交给图像模型自由生成；
5. 主题预览和正式上传必须分开，预览不能被误报成草稿创建；
6. 微信媒体接口失败时，需要区分预处理、渲染、媒体上传和草稿创建阶段。

本 Skill 将这些步骤串成一个可复用的本地工作流，并保留人工确认点。

## 支持的输入

### 1. URL 或网页来源

适合安全公告、技术博客、行业新闻、政策文件和产品发布页：

```bash
bash ./scripts/publish_draft.sh \
  --generate-from "https://example.com/security-advisory" \
  --out-dir ./output/bind-advisory \
  --topic "DNS 安全漏洞解读" \
  --markdown \
  --no-images
```

生成器会抓取来源、提取候选事实，并可用 `--search-query` 进行补充检索。网页抓取受 403、登录、反爬和 JavaScript 页面影响时，应先把内容保存为本地文件再运行。

### 2. 本地文件或素材

`--generate-from` 支持本地 Markdown、纯文本和其他可读取的文本材料。它可以处理原始摘录、会议纪要、研究笔记、产品说明和人工整理的素材：

```bash
bash ./scripts/publish_draft.sh \
  --generate-from ./notes/meeting-summary.txt \
  --out-dir ./output/article \
  --topic "把会议结论写成公众号文章" \
  --markdown \
  --no-images
```

如果输入本身已经是结构完整的 Markdown，生成器会尽量保留定稿内容，把主要工作放在图片规格和配图生成；普通文本则会根据事实池和主题提示组织为文章结构。

### 3. 当前对话、摘要或主题 brief

在 Comate 中，不必先手工创建 Markdown 文件。可以直接提供一段对话、会议总结、素材摘录或主题要求，例如：

```text
请把下面这段关于 BIND 9 漏洞的对话整理成一篇公众号文章，作者写赵涛，补充官方资料，生成封面和正文图，先预览不要上传。
```

对话入口由 Comate Skill 负责把当前上下文整理成生成输入，然后复用 URL/本地素材的同一套流程：提取事实、补充来源、生成文章、生成图片、人工检查和主题预览。直接来自对话的事实仍需区分“用户提供的信息”和“已由公开来源验证的信息”，不能把摘要中的推测自动写成确定事实。

### 4. 已有 Markdown

已有 `article.md` 时，可以跳过文章生成，直接进入准备、预览和草稿流程：

```bash
bash ./scripts/publish_draft.sh \
  ./article.md \
  --theme dns-sakura \
  --no-open-preview \
  --keep-temp
```

## Skill 构成

```text
tom-wechat-publisher/
├── SKILL.md                         # Comate 调用规则和安全契约
├── README.md                        # 本文档：安装、原理和使用说明
├── agents/
│   └── openai.yaml                  # Skill 在 Comate 中的显示信息和默认提示
├── assets/
│   ├── dns-sakura.css               # 公众号主题样式
│   ├── dns-daily.css
│   ├── dns-graphite.css
│   └── ...                          # 其他内置主题
└── scripts/
    ├── publish_draft.sh             # 主入口：准备、预览、上传和错误分层
    ├── prepare_wechat_md.py         # frontmatter、图片路径和图片格式准备
    ├── render_article_preview.py    # 轻量本地 HTML 预览
    ├── generate_article.py          # 从来源生成文章和图片规格
    ├── humanize_article.py          # 文章去模板化与评分
    ├── image_spec.py                # 从文章提取结构化配图规格
    ├── html_render.py               # 确定性正文信息图渲染器
    ├── compare_body_images.py       # HTML/AI 正文图候选比较
    ├── ocr_validate.py              # 图片文字 OCR 校验
    └── ocr_check.swift              # macOS Vision OCR 执行器
```

### 核心组件

- `SKILL.md`：告诉 Comate 何时触发、如何处理上传授权、如何选择图片路线以及如何报告结果。
- `publish_draft.sh`：主编排器。它不承担文章写作，而是负责把准备、渲染和草稿创建串起来。
- `prepare_wechat_md.py`：读取 Markdown frontmatter，覆盖标题、作者或封面，解析本地图片，必要时转换为 PNG，并将素材复制到临时上传目录。
- `render_article_preview.py`：不依赖 Wenyan 的轻量预览器，适合检查 Markdown 结构和本地图片路径。
- `generate_article.py`：可选的文章生成流程，负责来源抓取、事实池、文章计划和图片规格；生成内容仍需人工核对事实。
- `image_spec.py`：从定稿文章中提取配图所需的结构化字段，包括结构类型、事实风险、锁定事实和 QA 规则。
- `html_render.py`：把版本、命令、清单、数字和长标签等高事实风险内容确定性渲染为图片，避免模型幻觉。
- `ocr_validate.py`：对封面和带文字图片进行 OCR 检查，尤其用于发现中文错字、额外标点和乱码。

## 工作原理

### 1. 准备阶段

```text
本地 article.md
      │
      ├─ 读取 frontmatter
      ├─ 解析 Markdown / HTML 图片引用
      ├─ 校验图片存在性和格式
      ├─ 转换不兼容图片为 PNG
      └─ 生成临时 article.md
```

原始文章不会被上传脚本直接修改。脚本会在临时目录中生成准备后的 Markdown，并将相对图片路径改为临时目录中的素材路径。

### 2. 预览阶段

```text
准备后的 Markdown + 主题 CSS
              │
              ├─ wenyan render
              └─ preview.html
```

预览默认只读，不会调用微信草稿接口。可以先比较多个主题，再明确选择一个主题用于后续草稿创建。

### 3. 上传阶段

```text
明确授权 + --upload
          │
          ├─ 读取 WECHAT_APP_ID
          ├─ 读取 WECHAT_APP_SECRET 或 macOS Keychain
          ├─ Wenyan 上传封面和正文图片
          ├─ Wenyan 调用 draft/add
          └─ 检查退出码和错误响应
```

脚本会同时检查进程退出码和输出内容。即使 Wenyan 返回退出码 0，只要输出包含明确的错误码或失败信息，也会将任务标记为失败。

## 安全边界

### 不会做的事情

- 不会在只有“写作”“排版”“预览”“检查”意图时上传；
- 不会发布或群发文章；
- 不会把预览文件称为已创建草稿；
- 不会把图片生成器当作事实数据库；
- 不会在公开仓库中保存 App Secret、Access Token 或个人 Keychain 内容。

### 上传前检查

上传前应确认：

- 标题、作者、封面和正文图片数量正确；
- 封面满足 AI 国风封面规则；
- 正文图片已完成图片格式、文字和内容检查；
- Wenyan 预览成功；
- 目标公众号账号明确；
- 用户明确要求创建草稿；
- `WECHAT_APP_ID` 和 `WECHAT_APP_SECRET` 可用。

## 安装

### 方式 A：作为 Comate Personal Skill

将 Skill 目录复制到个人 Skill 目录：

```bash
mkdir -p ~/.comate/skills
cp -R tom-wechat-publisher ~/.comate/skills/
```

在已有 Skill 目录中更新时，建议先备份，再替换目录内容：

```bash
mv ~/.comate/skills/tom-wechat-publisher ~/.comate/skills/tom-wechat-publisher.backup
cp -R tom-wechat-publisher ~/.comate/skills/
```

不要把它安装到 `~/.comate/skills/.system/`，该目录由 Comate 管理。

### 方式 B：作为本地命令使用

不安装到 Skill 目录也可以直接运行：

```bash
bash ./scripts/publish_draft.sh /absolute/path/article.md --render-only
```

### 系统依赖

基本准备和预览需要：

- Python 3.9 或更高版本；
- Node.js 与 Wenyan CLI；
- macOS 的 `sips`，用于将不兼容图片转换为 PNG。

默认使用 `python3`。需要固定解释器时，设置 `TOM_WECHAT_PYTHON`，例如：

```bash
export TOM_WECHAT_PYTHON=/usr/bin/python3
```

macOS OCR 校验还需要：

- Swift；
- macOS Vision 框架。

安装 Wenyan CLI：

```bash
npm install -g @wenyan-md/cli
wenyan --version
```

如果只使用已经生成好的 Markdown 和图片，也可以跳过文章生成相关的可选依赖。

文章生成并自动配图时还需要 Comate 的 `create-image` Skill。默认脚本会从当前用户的 `~/.comate/skills/.system/`、`~/.agents/skills/.system/` 或 `~/.codex/skills/.system/` 中自动查找；也可以显式指定：

```bash
export TOM_WECHAT_CREATE_IMAGE_BASH="/path/to/create-image/scripts/generate-image.sh"
export TOM_WECHAT_CREATE_IMAGE_PS1="/path/to/create-image/scripts/generate-image.ps1"
```

不要把 `COMATE_USERNAME_ENCRYPTED` 或其他图片服务凭据写入仓库。

## 凭据配置

### 预览

预览不需要公众号 App Secret：

```bash
bash ./scripts/publish_draft.sh article.md \
  --theme dns-sakura \
  --no-open-preview \
  --keep-temp
```

### 创建草稿

上传时必须显式提供 App ID。App Secret 可以通过环境变量传入，或者放在 macOS Keychain 中：

```bash
export WECHAT_APP_ID="你的公众号AppID"
export WECHAT_APP_SECRET="你的公众号AppSecret"
```

然后：

```bash
bash ./scripts/publish_draft.sh article.md \
  --author "赵涛" \
  --theme dns-sakura \
  --theme-selection-confirmed \
  --upload
```

也可以使用 Wenyan 自己的凭据管理能力。具体命令以当前安装的 Wenyan CLI 版本为准：

```bash
wenyan credential
```

不要把 `WECHAT_APP_SECRET` 写入 Markdown、README、Shell 历史记录、Git 仓库或截图中。

## 快速开始

### 只准备和预览

```bash
bash ./scripts/publish_draft.sh \
  /absolute/path/article.md \
  --title "文章标题" \
  --cover /absolute/path/cover.jpg \
  --author "赵涛" \
  --theme dns-sakura \
  --require-body-images \
  --preview-dir /absolute/path/preview \
  --no-open-preview
```

`--preview-dir` 会持久保存 `preview.html` 及其引用的本地图片，不依赖稍后会被清理的临时目录。持久化预览统一按 UTF-8 读写，并确保 HTML 声明 `<meta charset="utf-8">`。准备阶段会自动校验本地封面文件、`cover_generation`、国画风格、题诗行数和排版字段。

### 比较多个主题

```bash
bash ./scripts/publish_draft.sh \
  /absolute/path/article.md \
  --preview-themes \
  --no-open-preview \
  --keep-temp
```

### 创建公众号草稿

仅在用户明确授权时使用：

```bash
WECHAT_APP_ID="你的公众号AppID" \
WECHAT_APP_SECRET="你的公众号AppSecret" \
bash ./scripts/publish_draft.sh \
  /absolute/path/article.md \
  --title "文章标题" \
  --author "赵涛" \
  --theme dns-sakura \
  --theme-selection-confirmed \
  --upload
```

上传时终端会先输出脱敏后的目标 AppID，例如 `test...p-id`，不会输出完整 AppID 或 App Secret。只有 Wenyan 输出明确成功措辞且包含可解析的 Media ID 时，脚本才会报告 `Draft Media ID`；退出码为 0 本身不是成功证据。Media ID 是草稿接口返回的素材标识，不代表文章已经发布或群发。

## 完整用法

### 命令行选项

```text
publish_draft.sh article.md
  --title TITLE
  --cover PATH
  --author NAME
  --theme THEME
  --highlight THEME
  --preview-themes
  --preview-dir DIR
  --no-open-preview
  --render-only
  --dry-run
  --keep-temp
  --theme-selection-confirmed
  --require-body-images
  --upload
```

### 文章生成

文章生成不是附加功能，而是本 Skill 的核心入口之一。可以把 URL、本地文本、会议纪要、摘要或人工整理的素材交给生成流程：

```bash
bash ./scripts/publish_draft.sh \
  --generate-from "https://example.com/source" \
  --out-dir /absolute/path/output \
  --topic "主题" \
  --markdown
```

生成流程会尝试补充来源事实，但不能代替人工核对。发布前应检查日期、数字、版本号、产品名、因果关系和引用。

### 人性化检查

```bash
bash ./scripts/publish_draft.sh \
  --humanize /absolute/path/article.md \
  --score
```

不带 `--in-place` 时不会覆盖原文件。需要覆盖时：

```bash
bash ./scripts/publish_draft.sh \
  --humanize /absolute/path/article.md \
  --in-place \
  --score
```

预览和上传前都会运行 `human-writing` 的 `check_prose.py`。默认从已安装的 Skill 路径查找；需要使用组织内副本时，设置 `TOM_WECHAT_CHECK_PROSE` 为 checker 的可执行路径。找不到 checker 或检查返回非零都会阻断流程，正文修改后必须重新检查。

### 图片规格和确定性信息图

定稿后可以生成图片规格：

```bash
python3 ./scripts/image_spec.py article.md --out image_specs.json
```

对于版本矩阵、命令、配置和清单等事实密集型内容，使用确定性 HTML 渲染：

```bash
python3 ./scripts/html_render.py image_specs.json \
  --out-dir /absolute/path/article-dir
```

正文图片候选比较：

```bash
python3 ./scripts/compare_body_images.py image_specs.json \
  --out-dir ./body-image-comparison \
  --skip-ai
```

技术文章在预览前执行正文图门禁：

```bash
bash ./scripts/publish_draft.sh article.md \
  --require-body-images \
  --theme dns-sakura
```

该门禁要求 `image_specs.json` 至少包含 3 个正文图规格，并检查文章中的本地图片引用、规格映射和所在章节是否一致。高风险 `html`/`hybrid` 规格必须提供 `locked_facts`，泛化标签会被拒绝；`hybrid` 图片还必须标记 `overlay_applied: true`。仅生成封面的技术文章不得报告为完整图文稿。

失败通知默认关闭，避免一次本地校验失败产生隐式外部消息。需要通知时显式设置接收人：

```bash
export TOM_WECHAT_FAILURE_NOTIFY="your-infoflow-user"
```

### OCR 校验

封面或图片规格包含锁定文字时：

```bash
python3 ./scripts/ocr_validate.py \
  --image /absolute/path/image.jpg \
  --spec /absolute/path/cover_spec.json \
  --index 0
```

批量检查图片目录：

```bash
python3 ./scripts/ocr_validate.py \
  --image-dir /absolute/path/images \
  --spec /absolute/path/image_specs.json \
  --all
```

## 图片工作流

### 封面

封面必须是 AI 生成的中国画风格图片，支持的风格包括：水墨写意、工笔重彩、青绿山水、宋画风、敦煌壁画风、文人画和新中式国风。

新生成或重新生成的封面还需要：

- 2.35:1 构图；
- 恰好 4 行原创题诗，依次形成场景、张力、行动、后果；四行必须统一为每行 5 个或每行 7 个汉字；
- 题诗与画面一体生成；
- OCR 逐字校验；
- 题诗错误时优先进行图生图局部修复。

未改动的历史文章可以沿用恰好 2 行的旧题诗，仅用于兼容校验；新生成和重新生成不得使用两行。

### 正文图

正文图按照事实风险路由：

- `ai + low`：场景、气氛、空间关系和抽象隐喻；
- `html + high`：版本、数字、命令、配置、表格和清单；
- `hybrid + medium/high`：AI 生成视觉主体，再用确定性层叠加事实标签。

推荐顺序是：

```text
场景 → 机制 → 精确事实/条件 → 操作清单
```

不要让 AI 图片生成器承担版本号、命令参数、CVE 编号或长段文字的准确性。

## 默认作者

默认作者为 `赵涛`。优先级如下：

```text
--author > WECHAT_AUTHOR > 赵涛
```

示例：

```bash
# 使用默认作者赵涛
bash ./scripts/publish_draft.sh article.md --dry-run

# 使用环境变量覆盖
WECHAT_AUTHOR="其他作者" bash ./scripts/publish_draft.sh article.md --dry-run

# 使用单次命令覆盖，优先级最高
bash ./scripts/publish_draft.sh article.md --author "其他作者" --dry-run
```

## 故障排查

### `wenyan` 未找到

```bash
npm install -g @wenyan-md/cli
wenyan --version
```

### 图片找不到

检查 Markdown 图片路径是否相对于 `article.md` 所在目录，并确认文件存在：

```bash
ls -l images/
```

### `40113` 或媒体上传失败

按阶段排查：

1. 查看临时目录中的准备后 Markdown；
2. 确认封面和正文图片最终扩展名为 JPEG、PNG 或 GIF；
3. 检查图片尺寸、文件大小和是否为空文件；
4. 用 `--keep-temp` 保留上传工作目录；
5. 读取 Wenyan 的具体错误响应，不要盲目重复上传。

### 预览成功但上传失败

预览只证明 Markdown 和主题可以渲染，不证明公众号凭据、媒体接口或目标账号可用。分别检查：

- `WECHAT_APP_ID` 是否设置；
- `WECHAT_APP_SECRET` 是否正确；
- Wenyan 凭据是否指向正确公众号；
- 微信接口是否拒绝某个具体素材；
- 账号是否具有草稿创建权限。

### `named`、`querylog` 等技术内容如何处理

本 Skill 只负责文章素材流程，不验证文章领域事实。DNS、安全、漏洞和版本信息必须在文章定稿前由作者根据官方来源核对。

## 开发与验证

检查 Python 语法：

```bash
python3 -m py_compile scripts/*.py
```

检查 Shell 语法：

```bash
bash -n scripts/publish_draft.sh
```

不上传地运行基本链路：

```bash
bash ./scripts/publish_draft.sh \
  /absolute/path/article.md \
  --dry-run \
  --no-open-preview \
  --keep-temp
```

作者解析的三个回归场景：

1. 不设置 `WECHAT_AUTHOR`，结果应为 `赵涛`；
2. 设置 `WECHAT_AUTHOR`，结果应使用环境变量值；
3. 同时传入 `--author`，结果应使用命令行值。

正式上传测试必须使用测试账号或明确授权的公众号账号，不应在 CI 中保存生产 App Secret。

## 已知限制

- 上传依赖 Wenyan CLI 的当前版本和微信接口行为；
- `sips` 是 macOS 工具，Linux 需要自行替换图片转换实现；
- OCR 校验器使用 macOS Vision，非 macOS 环境需要替代 OCR 工具；
- AI 封面和低事实风险场景图仍需要人工视觉检查；
- `--generate-from` 的外部来源可能受网络、登录、反爬或页面渲染影响；
- 草稿创建成功不等于文章已经发布。

## 许可证与责任

本仓库提供的是本地自动化脚本和 Skill 指令。使用者应自行确认：

- 文章内容、图片和引用具备合法使用权；
- 公众号账号和微信接口凭据属于自己或已获得授权；
- 上传内容符合微信平台规则和所在组织的发布流程；
- 公开仓库中不提交任何密钥、Token、Cookie、Keychain 导出内容或个人文章素材。

建议在企业内部使用时增加组织自己的审核、脱敏和账号权限控制。

# tom-wechat-publisher

一个面向微信公众号文章的 Markdown 准备、图片校验、主题预览和草稿创建 Skill。

它把一篇本地 Markdown 文章处理成适合微信公众号的素材：规范化 frontmatter，准备封面和正文图片，渲染主题预览，并在用户明确授权后通过 Wenyan 创建公众号草稿。

> 该 Skill 只创建草稿，不发布、不群发。`--upload` 是外部副作用开关，必须由用户明确请求时才使用。

## 目录

- [它解决什么问题](#它解决什么问题)
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

- Python 3；
- Node.js 与 Wenyan CLI；
- macOS 的 `sips`，用于将不兼容图片转换为 PNG。

macOS OCR 校验还需要：

- Swift；
- macOS Vision 框架。

安装 Wenyan CLI：

```bash
npm install -g @wenyan-md/cli
wenyan --version
```

如果只使用已经生成好的 Markdown 和图片，也可以跳过文章生成相关的可选依赖。

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
  --keep-temp
```

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

创建成功后，终端会输出 Wenyan 返回的 Media ID。Media ID 是草稿接口返回的素材标识，不代表文章已经发布或群发。

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
  --no-open-preview
  --render-only
  --dry-run
  --keep-temp
  --theme-selection-confirmed
  --upload
```

### 文章生成

可以把 URL 或本地来源交给可选生成流程：

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

封面还需要：

- 2.35:1 构图；
- 2 行或 4 行原创题诗；
- 题诗与画面一体生成；
- OCR 逐字校验；
- 题诗错误时优先进行图生图局部修复。

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

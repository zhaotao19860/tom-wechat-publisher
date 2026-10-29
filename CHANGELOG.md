# Changelog

本文件记录 `tom-wechat-publisher` 的重要能力变更。

## 2026-10-29

### 新增

- 新增 `scripts/cover_image.py`：只依赖 macOS `sips` 的封面工具。`crop` 保留全宽、按 `--anchor`（top/center/bottom/0..1）裁成 2.35:1，再缩放到 1800x766，不覆盖原图；`check` 检查封面是否为 1800x766，不是则返回 1。此前 agent 为裁封面反复调用 Pillow，系统 Python 没有安装，`pip install` 又被 PEP 668 拦截。SKILL.md 封面第 4 步写明用法。
- SKILL.md 新增 “Article footer” 一节：文末统一引用 `~/Desktop/wechat/images/footer-guofeng-seal.png`，放在 `## 参考资料` 之后、作为最后一张图，不写进 `image_specs.json`。此约定原来只存在于 Comate 记忆中，其他 agent 不知道。

- 入口输出运行标记，供 tom-workflow-optimize 切分每一次工作流运行：`check_topic_history.py` 开头向 stderr 输出 `[[wf-run start tom-wechat-publisher <run_id>]]`，`publish_draft.sh` 草稿创建成功后输出 `[[wf-run end tom-wechat-publisher <run_id> success]]`。`run_id` 由 `publish_draft.sh` 生成并经 `WECHAT_RUN_ID` 传给子脚本，单独调用时自动生成。标记只写 stderr，不影响 `--json` 输出。

### 改进

- `validate_body_images.py` 不再把页脚图当成未规划的正文配图：跳过页脚，要求页脚出现时必须是最后一张图，拒绝已停用的 `wechat.jpg` / `wechat-sign.jpg`；旧文章为绕过校验写进 specs 的页脚条目也一并忽略。用近期 19 篇带页脚的文章回归，全部通过。

## 2026-10-20

### 新增

- 讲解视频改用 Remotion 制作，替换原来的 Anatomy 等轴测录制流程。Anatomy 画面偏暗、看不到报文内容、节点只能横排，不适合讲协议交互。新画面白底高对比，被访问的服务器按顺序竖排，每一步一对带标签的查询、应答箭头，标出真实字段，缓存写入和命中直接显示在画面上，底部有字幕条。
- 新增 `assets/remotion-explainer-template/`：一个可直接复用的 Remotion 工程，示例是同一台 BIND9 先冷缓存解析 www.baidu.com、再热缓存解析 www.qq.com，共 18 步。节点、报文、字幕和缓存记录都放在 `src/steps.ts` 一个数据文件里。
- 新增 `scripts/video_schedule.py`：把各段旁白时长换算成 `schedule.json`。每步时长取 `max(4.0, 旁白 + 0.15 + 0.8)` 秒，支持多场景，场景前后留停顿，可选给每次查询加一声轻点击音。Remotion 工程直接 import 这个文件，让每一步跟旁白对齐。

### 改进

- `scripts/video_render.py` 改为调用 `npx remotion render --muted` 渲染无声画面，再按时间轴混入旁白和音效，响度归一到 −16 LUFS 后输出 1280 宽 H.264 + AAC。去掉对 Anatomy `drive.mjs` 逐帧截图的依赖。
- GIF 新增 `--gif-fps`。动图超过 300 帧会被微信拒收，脚本会自动提高 GIF 倍速，把帧数压到 300 以内。
- `video_narrate.py` 的用法改为配合 `--durations` 直接写出 `narration.json`，再交给 `video_schedule.py`。
- `SKILL.md` 和 `references/video-explainer.md` 改写为 Remotion 流程，补充版式约定、旁白读法（如 `点 com`、IP 逐位念、长随机标签只放画面不念）、渲染前的静帧检查，以及“视频放在文章开头”时的留位写法。
- 单元测试把原来针对 Anatomy 逐帧截图的用例换成时间轴用例，覆盖步长下限、场景停顿、音效时刻和场景步数不匹配时报错。

## 2026-10-19

### 新增

- 新增讲解视频流程第一版：Anatomy 画面加 edge-tts 神经语音配音（默认 `zh-CN-YunxiNeural`），不使用机械感较重的 macOS `say`。新增 `scripts/video_narrate.py`（逐句合成、去首尾静音、测量时长）和 `scripts/video_render.py`（录制、混音、输出 MP4 和无声 GIF）。
- 明确接口上传的视频素材不能嵌入图文正文，MP4 需要在网页编辑器里用「本地上传」插入。

### 改进

- Wenyan 的链接脚注尾注默认关闭，新增 `--footnote` 按需开启。文章自带 `## 参考资料` 时，尾注会把每个链接重复一遍，以网址作锚文本的链接还会渲染出 `undefined:`。

## 2026-09-25

### 改进

- 封面画风由随机抽取改为**按顺序轮换**：用户未指定画风时，取七种国画风格里紧接上一篇所用风格的下一种，用到最后一种后回到第一种，循环推进，连续文章不重复直到走完整轮。新增 `scripts/next_cover_style.py` 扫描历史根目录里各文章 frontmatter 的 `cover_style`，按日期与 mtime 找出最近使用的风格并打印下一种；`SKILL.md` 的封面步骤 1、2 同步改写，风格列表顺序即轮换顺序，并与脚本 `DEFAULT_STYLES` 保持一致。日期解析只认 `20YY-MM(DD)` 前缀，避免 CVE 号、AS 号等非日期目录名污染排序。

## 2026-09-23

### 新增

- 正文配图新增 `mermaid` 后端。结构类与关系类图（流程、关系、时间线、对照、清单）改由 mermaid 自动布局，并用 Playwright 全页截图（自适应高度）出图，去掉固定 16:9 画布带来的大面积留白。所有标签直接来自 spec，事实逐字保真。
- 配图后端改为“按内容选最合适的”：`image_spec.py` 把结构/关系/流程/对照/时间线/清单路由到 `mermaid`；真正的数值序列（≥5 个可比较数值）保留 `html` 条形图；场景与影响图仍走 `ai`（小黑）。
- `html_render.py` 新增 `build_mermaid_source`、`render_mermaid_png`、`render_png_fullpage`；`render_specs` 按后端分流（mermaid / html，跳过 AI 槽位）。`validate_body_images.py` 支持 `render_mode: mermaid` 并校验 `mermaid-render` 后端。
- mermaid 渲染依赖 `mermaid` npm 包（记录在 `package.json`），复用 Playwright 已有的 Chromium，无需额外浏览器下载。

## 2026-09-16

### 改进

- 封面画风改为随机选择：用户未指定画风时，从七种国画风格中等概率随机抽取一种，用真随机器而非模型判断，避免长期偏向青绿山水，并报告抽中的风格；同时要求画面场景与主体贴合抽中的画风，不再一律套用山水构图。
- 多主题预览（`--preview-themes`）现在遵循 `--preview-dir`，把每个主题的预览按 `article.<theme>.html` 落到持久目录；`persist_preview.py` 支持自定义输出文件名，并修复主题选择器内联 Python 片段中一段无匹配 `try` 的 `except` 造成的语法错误。
- S11 流程路径版式的节点现在渲染 `sub` 副标题，新增 `.node-sub` 样式。

### 文档

- 更新 `SKILL.md` 封面契约示例，去除示例全部为青绿山水的默认偏向，改为工笔重彩与水墨写意，并将题款排布说明由 landscape rhythm 改为通用的画面构图节奏。

## 2026-08-09

### 改进

- 封面题诗契约改为新生成和重新生成必须使用四行绝句式结构，四行依次承载场景、张力、行动和后果，并统一为每行 5 个或每行 7 个汉字；未改动历史文章的二行题诗仅保留为兼容校验路径。
- 补充 `TOM_WECHAT_CHECK_PROSE` 覆盖入口、脱敏 AppID、带明确成功证据的 Media ID 输出，以及 UTF-8 持久化预览说明。

## 2026-07-24

### 新增

- 为所有文章增加 AI 国画封面契约：限定国画风格、题诗、书写方向和本地封面素材，并在预览与上传前统一校验。历史兼容校验仍允许未改动旧文章保留二句题诗。
- 新增正文混合配图流程：低事实风险场景使用 Ian 小黑插图，高事实风险内容使用确定性 HTML 信息图，混合模式保留确定性文字覆盖层。
- 将正文第一张图固定为 Ian 小黑 `ai + low` 视觉锚点，并在图片规格生成和上传预检阶段阻止非小黑首图。
- 新增 `validate_body_images.py`，校验正文图片数量、文件引用、章节位置、渲染路由、通用标签、锁定事实和混合覆盖层。
- 新增 `validate_article_assets.py`，校验封面文件及 `cover_generation`、`cover_style`、`cover_poem`、`cover_typography` 元数据。
- 新增 `persist_preview.py` 与 `--preview-dir`，将预览 HTML 及本地图片保存到稳定目录。
- 新增正文图片候选复用与 `pending-ai` 状态，AI 必选槽位不再静默回退为 HTML。
- 新增单元测试，覆盖封面契约、小黑首图、正文图片路由、OCR、持久预览、上传通知安全和 Python 3.9 兼容性。

### 改进

- 图片规格现在记录实际 `illustration_backend`、`rendered_by` 和 `overlay_applied`，使预览与上传预检可以发现过期或错误路由的素材。
- OCR 按图片类型执行差异化校验：封面核对题诗，低风险 AI 正文图不再误将文章标题作为必需文字，HTML 模板文字可加入允许列表。
- 正文图片比较器只为低风险 AI 槽位生成或复用小黑候选，精确数字、版本、命令和配置继续走确定性渲染。
- 上传脚本增加 Python 3.9+ 检查、正文图片强校验、封面资产预检和分阶段错误上下文。
- 如流失败通知改为显式配置后才启用，避免默认向固定收件人发送消息。
- 修复 Python 3.9 下嵌套 f-string 和类型标注的兼容性问题。

### 文档

- 更新 `SKILL.md`、`README.md` 和 `agents/openai.yaml`，同步封面、正文首图、事实锁定、持久预览和草稿上传规则。

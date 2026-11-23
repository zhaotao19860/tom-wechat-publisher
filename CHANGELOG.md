# Changelog

本文件记录 `tom-wechat-publisher` 的重要能力变更。

## 2026-11-23

### 新增

- 正文长度上限：`voice_check.py` 新增失败项 `too_long`，正文汉字（不算 frontmatter、代码块、图片行、HTML、URL 和 `## 参考资料` 及之后）超过上限就失败。上限取 `--max-han`，否则 `TOM_WECHAT_MAX_HAN`，否则 1500；0 关闭。`--json` 报告多一个 `max_han` 字段。
- 账号 profile 新增可选字段 `max_han`（≥0 的整数，默认不设），导出为 `PROFILE_MAX_HAN`；`publish_draft.sh` 有值时传 `--max-han`，即 profile 的值优先于 `TOM_WECHAT_MAX_HAN`。
- `generate_article.py` 用同样的口径数正文汉字，超过上限时在 stderr 打 `[generate-article:length] WARNING`（不拦截，拦截交给 voice_check）。
- `SKILL.md` Before drafting 新增第 4 条：按 1500 汉字以内规划（一条主线、两三节、少背景），超长时整段删掉不在主线上的段落或小节，不逐句压缩；`references/harsh-editor.md` 的「不许做」同步加上这一条。

## 2026-11-22

### 新增

- `scripts/voice_check.py`（只用标准库，`python3 -I`）：检查 `check_prose.py` 看不到的篇章形状和说话姿态。失败项是套话框架（我原本以为、写得很硬、到这里为止等）、「A，B 才/就/还得 C」式结论句小标题、没有出处的亲历（我第一件事就是、我见过的人等）；提醒项是 voice facts 里对不上的第一人称经历、句长 CV < 0.55（至少 30 句）、节长 CV < 0.45（至少 3 节）、H2 多于 4 个、1200 汉字以上全文无问号、段尾短判断（末句 ≤ 18 汉字且含就/才/不/没/都/算/只/也/得）超过一半（至少 6 段）、顿号每千字 > 12、术语解释 ≥ 2 处。`--facts` 读可用第一人称事实清单，句子包含某行或与某行关键词重合（≥ 2 个且 ≥ 60%）即算有出处。`--mode block`（默认）有失败项退出 1，`--mode warn` 总是退出 0；`--json` 输出机器可读报告。阈值在作者 2021 年博客 63 篇上 0 失败（9 篇只有顿号或 H2 提醒），2026-10/11 的 50 篇公众号 21 篇有失败项。
- `publish_draft.sh` 在 `check_prose.py` 之后跑 `voice_check.py`。`TOM_WECHAT_VOICE_CHECK=warn|block|off`，默认 `warn` 只打印不拦截；`--facts` 取 `TOM_WECHAT_VOICE_FACTS`，没有时取账号 profile 的 `voice_facts`。
- 账号 profile 新增可选字段 `voice`、`voice_facts`（风格卡和第一人称事实清单的路径，展开 `~`，相对路径按 `accounts/` 解析，写了但文件不存在就报错），导出为 `PROFILE_VOICE`、`PROFILE_VOICE_FACTS`。
- `references/harsh-editor.md`：定稿后、`check_prose.py` 之前在新上下文里跑一轮「毒舌编辑」改稿，作者和口气以风格卡为准。
- `SKILL.md`：Before drafting 增加第 5 条（风格卡和第一人称事实清单优先于 human-writing 默认文风，agent 跑的命令不写「我」）；Enforced revision gate 增加毒舌编辑轮和 `voice_check.py`。

### 修正

- `--generate-from` 不再默认跑 humanize，需要时显式加 `--humanize`。
- `humanize_article.py` 删掉 `_split_rigid_paragraphs`（会插入「这一点很重要。」）和 `_swap_three_part_lists`（把「A、B和C」删成「A和B」，丢事实）。
- `generate_article.py` 兜底模板去掉「不在于……而是……」翻案句和「## 一句话总结」小节（通用、CVE、DMARC、封禁清单四类模板）。

- 可插拔图片后端 `scripts/image_backend.py`：命令行和退出码与 `create-image` 的 `generate-image.sh` 一致，按 `TOM_WECHAT_IMAGE_BACKEND`、账号 profile 的 `image_backend`、默认 `comate` 的顺序选择后端。`comate` 原样 exec 内网脚本；`openai` 走 OpenAI 兼容的 `/images/generations` 和 `/images/edits`；`gemini` 走 `models/<model>:generateContent` 并解码内联图片；`command` 按 `TOM_WECHAT_IMAGE_COMMAND` 模板调用任意工具。密钥读 `TOM_WECHAT_IMAGE_API_KEY` 或钥匙串服务 `tom-wechat-publisher-image`，从不打印。`--check [--json]` 报告选中的后端和缺少的设置。
- 账号 profile 新增可选字段 `image_backend`，`accounts/medical.json` 留空。

### 改进

- `generate_article.py`（以及调用它的 `compare_body_images.py`）改为通过 `image_backend.py` 出图，生成前的可用性检查改问 `image_backend.py --check`；参数、依赖、配置、输入类错误（退出码 2/3/4/5）不再重试。`TOM_WECHAT_CREATE_IMAGE_BASH`/`_PS1` 继续覆盖 comate 脚本路径。不设新变量时本机行为不变。
- `publish_draft.sh` 导出 `TOM_WECHAT_ACCOUNT`，让子进程读到当前账号的 `image_backend`；`check_prose.py` 查找路径加入 `~/.claude/skills/human-writing/scripts/check_prose.py`。
- 补测试：图片后端的请求结构、选择优先级、`--check`、退出码；如流通知在 `infoflow-message-group` 缺失时跳过并保留原退出码。

## 2026-11-15

### 新增

- 多账号 profile：新增 `scripts/account_profile.py` 和 `accounts/medical.json`。`publish_draft.sh --account medical` 会按账号解析 AppID/AppSecret 环境变量名、Keychain 服务、作者、默认主题、历史根目录、合集、页脚和默认内容线；上传前打印 `Target account` 和掩码 AppID。不带 `--account` 时技术号行为不变。
- 内容线 `medical`：眉标 `MED PRACTICE`、栏目「临床笔记」、合集「医学科普库」，并加入疾病、药物、剂量、指南等术语，供配图标签提取。
- 医学号视觉：封面支持临床插画、医学水彩、临床信息图，题诗可选且不自动生成；正文首图改用临床插画，不再使用小黑卡通；确定性信息图的英文眉标在医学号下改为中文。
- 新增 `assets/med-clinical.css`、`assets/med-warm-paper.css`、`assets/med-quiet-blue.css` 三套正文主题。
- `next_cover_style.py` 新增 `--track medical`，医学号按临床插画风格轮换。
- `generate_article.py` 在 `--track medical` 时使用门诊结构（门诊常问、机制、需要留意的人、日常处理、就医提示、常见误区）和临床插画提示词。

### 改进

- `prepare_wechat_md.py` 和 `validate_article_assets.py` 的封面契约按 track 分支：技术号仍要求国画风格与 2/4 行题诗，医学号允许临床插画风格、空题诗和 `cover_typography: none`。
- `validate_body_images.py` 支持 `--footer`/`TOM_WECHAT_FOOTER_IMAGE` 按账号切换页脚；医学号首图必须是临床插画或确定性开场图，出现小黑会直接拦截。
- `SKILL.md`、`README.md` 补充账号 profile、医学号封面、题诗可选和首图规则。

## 2026-11-11

### 新增

- 网关文章漏写 track 时提醒：`tracks.py hint ARTICLE.md` 在 frontmatter 没有 track、正文命中两个以上四层或 AI 网关关键词时打印一行建议，`publish_draft.sh` 在合集提醒后调用它，只提醒不拦截。深金色封面眉标的后半句按 track 取值（dns「安全观察」，网关「架构实践」）。
- 新增内容线 `track`：article.md frontmatter 可写 `track: dns | l4-gateway | ai-gateway`，缺省或未知值按 `dns` 处理，旧文章输出不变。新增 `scripts/tracks.py` 集中定义每条线的显示名、配图眉标（dns 仍为 `DNS Daily` / `DNS DAILY`，四层为 `L4 GATEWAY`，AI 网关为 `AI GATEWAY`）、术语表和推荐合集（dns→DNS知识库，两条网关线→网关知识库）。
- `image_spec.py` 把 frontmatter 的 track 写进 `image_specs.json`（无 track 时不写该键），网关线在 DNS 术语表之前先匹配四层或 AI 网关术语；英文短词按词边界匹配，避免 RS、DR、NAT 误命中普通单词。`html_render.py` 的三处封面眉标改为按 track 取值。
- `generate_article.py` 新增 `--track`，`publish_draft.sh` 新增 `--track` 并透传给生成模式。`publish_draft.sh` 在草稿上传成功后、以及 dry-run/render-only/未加 `--upload` 结束时打印 `Album reminder: 发表时请在后台选择合集「...」`，因为草稿 API 设置不了合集。
- `check_topic_history.py` 的每条历史命中附带该文章的 `track`；查重算法不变。

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

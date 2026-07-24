# Changelog

本文件记录 `tom-wechat-publisher` 的重要能力变更。

## 2026-07-24

### 新增

- 为所有文章增加 AI 国画封面契约：限定国画风格、二句或四句题诗、书写方向和本地封面素材，并在预览与上传前统一校验。
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

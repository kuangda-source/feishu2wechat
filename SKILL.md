---
name: feishu2wechat
version: 1.0.0
description: 飞书文档自动转微信公众号草稿。给定 feishu.cn/wiki 或 feishu.cn/docx 链接后，读取飞书文档、下载并上传图片、用 viral/standard 模板排版、创建测试或正式公众号草稿并发送 webhook 通知。
author: custom
tools: [filesystem, http, shell, feishu_doc]
trigger: "飞书转公众号 | 飞书文档发布公众号 | 论文推文 | 公众号草稿 | feishu.cn/wiki | feishu.cn/docx"
priority: 100
---

# 飞书文档自动发布到微信公众号

## 使用原则

- 默认只发布到测试公众号。确认 `config.json` 中 `_comment` 为 `当前使用版本：test`。
- 不要跳过测试版直接切正式版。只有用户明确审核通过后，才把 `_comment` 改成 `当前使用版本：production` 并重新运行。
- 不提交或展示真实 `config.json`、AppSecret、webhook URL、草稿内容缓存或下载图片。

## 快速命令

```bash
cd <skill-dir>
python3 -m pip install -r scripts/requirements.txt
python3 scripts/feishu-wechat-auto.py "<飞书链接>"
```

预览模式不调用微信 API、不上传图片、不发 webhook：

```bash
python3 scripts/feishu-wechat-auto.py "<飞书链接>" --dry-run
```

## 配置

从模板复制：

```bash
cp config.json.example config.json
```

关键字段：

- `_comment`: `当前使用版本：test` 或 `当前使用版本：production`
- `test.wechat`: 测试公众号 AppID/AppSecret
- `production.wechat`: 正式公众号 AppID/AppSecret
- `feishu`: 飞书开放平台 AppID/AppSecret
- `template`: 默认 `viral`

也可用环境变量指定配置路径：

```bash
FEISHU_WECHAT_CONFIG=/path/to/config.json python3 scripts/feishu-wechat-auto.py "<飞书链接>"
```

## 工作流

1. 从飞书 wiki/docx 链接提取文档 token。
2. 读取飞书标题、blocks、图片，按原顺序重建 Markdown。
3. 下载飞书图片并转换为 JPG。
4. 生成 `wechat-output/article.md`。
5. 非 dry-run 时上传本地图片到微信素材接口并生成 `article_uploaded.md`。
6. 调用内置 `scripts/publish_wechat.py` 创建公众号草稿。
7. 成功后向当前版本 webhook 发送通知。

## 排版优化

`viral` 模板在 Markdown 转 HTML 后统一增强：

- H2 章节标题增加 `SECTION 01` 编号、宋体/衬线字体栈和强调色块。
- H3 使用黑体小标题、橙色圆点和底部分隔线。
- `👉`、`📌`、`✅`、`⚠️`、`💡`、`一句话概括` 等重点段落转换为提示卡片。
- 图片增加圆角、阴影和图注。
- 表格增加表头底色、边框和斑马纹。
- 正文中以 `第一，`、`第二，`、`第三类是` 等开头的分点句会拆成独立段落，并在前面加 `·`。

## 常见阻塞

- 微信返回 `40164 invalid ip`: 当前机器出口 IP 未加入公众号 IP 白名单。把错误里的 IP 加到当前版本公众号后台白名单后重试。
- 飞书读取失败：检查飞书应用权限是否包含 `docx:document`、`wiki:wiki`、`drive:file`。
- 图片不显示：确认非 dry-run 已成功上传图片并使用 `article_uploaded.md` 发布。

## 内置脚本

- `scripts/feishu-wechat-auto.py`: 主入口。
- `scripts/upload_images_to_wechat.py`: 上传 Markdown 中本地图片并替换为微信 URL。
- `scripts/publish_wechat.py`: Markdown/URL 到微信公众号草稿的发布器，已内置排版模板。
- `scripts/test_publish_wechat_format.py`: viral 模板回归测试。

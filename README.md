# feishu2wechat

一体化 Codex/OpenClaw skill：把飞书 Wiki/Docx 文档自动读取、整理排版，并发布到微信公众号草稿箱。

它把原来的 `feishu-wechat-auto-publisher` 和 `wechat-article-publisher` 合并成了一个自包含 skill，不再依赖外部发布器目录。

## 功能

- 读取飞书 `wiki` / `docx` 链接的标题、正文 blocks 和图片
- 保留图片在原文中的位置，并自动下载、转成 JPG
- 非预览模式下自动上传本地图片到微信公众号素材接口
- 创建微信公众号草稿，不默认群发
- 支持测试版 / 正式版双配置
- 发布成功后可发送飞书群 webhook 通知
- 内置 `viral` 和 `standard` 两套微信 HTML 模板

## 排版优化

`viral` 模板内置当前优化版样式：

- 章节标题增加 `SECTION 01` 编号和字体层级
- 正文使用更舒展的字号、行高和字距
- 重点句自动转成提示卡片
- 图片增加圆角、阴影和图注
- 表格增加表头底色、边框和斑马纹
- 正文中的 `第一，`、`第二，`、`第三类是` 等分点句自动拆段，并在前面加 `·`

## 安装

```bash
git clone git@github.com:kuangda-source/feishu2wechat.git
cd feishu2wechat
python3 -m pip install -r scripts/requirements.txt
```

## 配置

复制模板：

```bash
cp config.json.example config.json
```

填入：

- `test.wechat.app_id` / `test.wechat.app_secret`
- `production.wechat.app_id` / `production.wechat.app_secret`
- `feishu.app_id` / `feishu.app_secret`
- `test.webhook` / `production.webhook`

默认请保持：

```json
{
  "_comment": "当前使用版本：test",
  "template": "viral"
}
```

也可以通过环境变量指定配置路径：

```bash
FEISHU_WECHAT_CONFIG=/path/to/config.json python3 scripts/feishu-wechat-auto.py "<飞书链接>"
```

## 使用

预览模式，不调用微信接口、不上传图片、不发 webhook：

```bash
python3 scripts/feishu-wechat-auto.py "https://xxx.feishu.cn/wiki/xxx" --dry-run
```

发布到当前配置版本的公众号草稿箱：

```bash
python3 scripts/feishu-wechat-auto.py "https://xxx.feishu.cn/wiki/xxx"
```

自定义标题：

```bash
python3 scripts/feishu-wechat-auto.py "https://xxx.feishu.cn/wiki/xxx" --title "自定义标题"
```

## 流程约束

默认只发布到测试公众号。正式发布前请先：

1. 确认 `config.json` 中 `_comment` 为 `当前使用版本：test`
2. 运行脚本创建测试草稿
3. 在公众号后台检查排版、图片、标题和摘要
4. 用户确认后再切换到 `当前使用版本：production`

不要把真实 `config.json`、AppSecret、webhook URL、预览 HTML、图片缓存或草稿产物提交到仓库。

## 常见问题

### 微信返回 `40164 invalid ip`

当前机器出口 IP 没有加入微信公众号后台 IP 白名单。把错误信息里的 IP 加到当前版本公众号的白名单后重试。

### 图片不显示

确认不是 `--dry-run`，并检查日志里是否出现：

```text
✅ 已上传：image_x.jpg -> 微信图片 URL
```

### 飞书读取失败

确认飞书应用已开通文档和文件读取权限，至少需要：

- `docx:document`
- `wiki:wiki`
- `drive:file`

## 文件结构

```text
.
├── SKILL.md
├── config.json.example
├── agents/openai.yaml
└── scripts
    ├── feishu-wechat-auto.py
    ├── publish_wechat.py
    ├── upload_images_to_wechat.py
    ├── test_publish_wechat_format.py
    └── requirements.txt
```

## 测试

```bash
python3 -m py_compile scripts/feishu-wechat-auto.py scripts/upload_images_to_wechat.py scripts/publish_wechat.py scripts/test_publish_wechat_format.py
python3 scripts/test_publish_wechat_format.py
```

## License

MIT

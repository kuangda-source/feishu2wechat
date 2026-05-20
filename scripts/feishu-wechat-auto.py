#!/usr/bin/env python3
"""
飞书文档自动转公众号推文
功能：智能标题识别、PDF 自动截图、内容排版发布、自动选择封面
"""

import argparse
import json
import os
import subprocess
import sys
import re
import requests
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# 配置路径
SKILL_DIR = Path(__file__).parent.parent
CONFIG_CANDIDATES = [
    Path(os.environ["FEISHU_WECHAT_CONFIG"]) if os.environ.get("FEISHU_WECHAT_CONFIG") else None,
    SKILL_DIR / "config.json",
    SKILL_DIR.parent / "config.json",
    SKILL_DIR.parent.parent / "config.json" if SKILL_DIR.parent.name == "skills" else None,
]
CONFIG_FILE = next((path for path in CONFIG_CANDIDATES if path and path.exists()), SKILL_DIR / "config.json")

DEFAULT_WORKSPACE = Path("/root/.openclaw/workspace") if Path("/root/.openclaw/workspace").exists() else SKILL_DIR
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(DEFAULT_WORKSPACE)))
PDF_DIR = WORKSPACE / "pdfs"
SCREENSHOT_DIR = WORKSPACE / "screenshots"
OUTPUT_DIR = WORKSPACE / "wechat-output"
IMAGES_DIR = OUTPUT_DIR / "images"

# 论文标题特征词
PAPER_TITLE_KEYWORDS = [
    '论文', '研究', 'framework', 'learning', 'model', 'based',
    'deep', 'neural', 'network', 'auto', 'self-supervised',
    'TFR', 'CVPR', 'ICCV', 'ECCV', 'NeurIPS', 'ICML', 'IROS', 'ICRA',
    'arXiv', 'journal', 'conference', 'survey', 'review',
    '：', '|', '——', '-', ':', '202', '201'
]

def load_config():
    """加载配置"""
    if not CONFIG_FILE.exists():
        config = {
            "_comment": "当前使用版本：test",
            "test": {
                "wechat": {"app_id": "", "app_secret": ""},
                "webhook": ""
            },
            "production": {
                "wechat": {"app_id": "", "app_secret": ""},
                "webhook": ""
            },
            "feishu": {"app_id": "", "app_secret": ""},
            "author": "RobotQu",
            "original": True,
            "template": "viral"
        }
        CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        return config
    return json.loads(CONFIG_FILE.read_text())

def get_active_config(config):
    """获取当前激活的配置（test 或 production）"""
    version = config.get('_comment', '').replace('当前使用版本：', '').strip()
    if not version or version not in ['test', 'production']:
        version = 'test'  # 默认使用测试版
    
    active = config.get(version, {})
    wechat_config = active.get('wechat', {})
    webhook_url = active.get('webhook', '')
    
    # 合并通用配置
    merged = {
        'wechat': {
            'app_id': wechat_config.get('app_id', ''),
            'app_secret': wechat_config.get('app_secret', '')
        },
        'feishu': config.get('feishu', {}),
        'author': config.get('author', 'RobotQu'),
        'original': config.get('original', True),
        'template': config.get('template', 'viral'),
        'webhook': webhook_url,
        'version': version
    }
    
    return merged, version

def get_feishu_tenant_access_token(app_id, app_secret):
    """获取飞书 tenant access token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}
    resp = requests.post(url, json=payload, timeout=10)
    data = resp.json()
    if data.get("code") == 0:
        return data["tenant_access_token"]
    raise Exception(f"获取飞书 token 失败：{data}")

def extract_doc_token(url):
    """从飞书链接提取 doc_token"""
    patterns = [r'/docx/([\w-]+)', r'/wiki/([\w-]+)']
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def read_feishu_doc(doc_token, config, feishu_host=None):
    """读取飞书文档内容和图片"""
    print(f"📄 读取飞书文档：{doc_token}")
    
    feishu_config = config.get("feishu", {})
    app_id = feishu_config.get("app_id", "")
    app_secret = feishu_config.get("app_secret", "")
    
    if not app_id or not app_secret:
        print("❌ 未配置飞书 API")
        return None
    
    try:
        token = get_feishu_tenant_access_token(app_id, app_secret)
    except Exception as e:
        print(f"❌ 获取 token 失败：{e}")
        return None
    
    token_header = f"Bearer {token}"
    
    # 使用 wiki API 获取文档标题（更准确）
    title = ""
    wiki_url = f"https://open.feishu.cn/open-apis/wiki/v1/nodes/{doc_token}"
    wiki_resp = requests.get(wiki_url, headers={"Authorization": token_header}, timeout=10)
    if wiki_resp.status_code == 200:
        try:
            wiki_data = wiki_resp.json()
            title = wiki_data.get("data", {}).get("title", "")
            print(f"📋 Wiki API 获取标题：{title[:60]}..." if len(title) > 60 else f"📋 Wiki API 获取标题：{title}")
        except Exception as e:
            print(f"⚠️ Wiki API 解析失败：{e}")
    
    # 如果 wiki API 失败，尝试 docx API
    if not title:
        docx_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}"
        docx_resp = requests.get(docx_url, headers={"Authorization": token_header}, timeout=10)
        if docx_resp.status_code == 200:
            try:
                docx_data = docx_resp.json()
                # 标题在 data.document.title 中
                title = docx_data.get("data", {}).get("document", {}).get("title", "")
                print(f"📋 Docx API 获取标题：{title[:60]}..." if len(title) > 60 else f"📋 Docx API 获取标题：{title}")
            except Exception as e:
                print(f"⚠️ Docx API 解析失败：{e}")
    
    # 获取文档所有 blocks（支持分页）
    all_blocks = []
    page_token = ""
    blocks_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/blocks"
    
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        
        blocks_resp = requests.get(blocks_url, headers={"Authorization": token_header}, params=params, timeout=30)
        if blocks_resp.status_code != 200:
            print(f"❌ 获取 blocks 失败：{blocks_resp.status_code}")
            return None
        
        blocks_data = blocks_resp.json()
        if blocks_data.get("code") != 0:
            print(f"❌ API 返回错误：{blocks_data}")
            return None
        
        items = blocks_data.get("data", {}).get("items", [])
        all_blocks.extend(items)
        
        # 检查是否有下一页
        if not blocks_data.get("data", {}).get("has_more", False):
            break
        page_token = blocks_data.get("data", {}).get("page_token", "")
    
    print(f"📊 共获取 {len(all_blocks)} 个 blocks")
    
    # 从 blocks 重建内容（同时下载图片）
    output_dir = OUTPUT_DIR / "images"
    content = build_content_from_blocks(all_blocks, output_dir, token_header, feishu_host)
    
    return {
        "text": content,
        "title": title,
        "token": token
    }

def extract_images_from_blocks(blocks):
    """从 blocks 中提取图片信息（保留位置索引）"""
    images = []
    for idx, block in enumerate(blocks):
        if block.get("block_type") == 27:  # Image block
            image_info = block.get("image", {})
            if image_info.get("token"):
                images.append({
                    "token": image_info.get("token"),
                    "width": image_info.get("width", 0),
                    "height": image_info.get("height", 0),
                    "block_id": block.get("block_id"),
                    "block_index": idx  # 记录图片在 blocks 中的位置
                })
    return images

def postprocess_markdown(content):
    """后处理 Markdown，确保格式规范"""
    import re
    
    lines = content.split('\n')
    result = []
    in_list = False
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        is_list_item = stripped.startswith('- ') or stripped.startswith('* ') or re.match(r'^\d+\.', stripped)
        is_heading = stripped.startswith('#')
        is_empty = not stripped
        
        # 列表开始前加空行
        if is_list_item and not in_list:
            if result and result[-1].strip():
                result.append('')
            in_list = True
        
        # 列表结束后加空行
        if not is_list_item and in_list and result and result[-1].strip():
            # 如果当前行不是空行且不是列表项，说明列表结束了
            if not is_empty:
                result.append('')
            in_list = False
        
        # 标题前后加空行
        if is_heading:
            if result and result[-1].strip():
                result.append('')
            result.append(line)
            result.append('')  # 标题后加空行
            continue
        
        result.append(line)
    
    # 清理多余空行
    final = []
    prev_empty = False
    for line in result:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue
        final.append(line)
        prev_empty = is_empty
    
    return '\n'.join(final)

def build_content_from_blocks(blocks, output_dir, token_header, feishu_host=None):
    """从 blocks 重建内容，在正确位置插入图片"""
    print("📝 从 blocks 重建内容...")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    content_lines = []
    image_count = 0
    
    # 收集所有表格内的文本 block ID，避免重复
    table_text_ids = set()
    
    # 预先扫描，收集所有表格内的文本 block ID
    for block in blocks:
        if block.get("block_type") == 31:
            table_data = block.get("table", {})
            cell_ids = table_data.get("cells", [])
            for cell_id in cell_ids:
                for sub_block in blocks:
                    if sub_block.get("block_id") == cell_id and sub_block.get("block_type") == 32:
                        cell_children = sub_block.get("children", [])
                        for text_child_id in cell_children:
                            table_text_ids.add(text_child_id)
    
    for block in blocks:
        block_type = block.get("block_type")
        
        # 表格 block (类型 31) - 整个表格
        if block_type == 31:
            table_data = block.get("table", {})
            prop = table_data.get("property", {})
            column_size = prop.get("column_size", 3)  # 列数
            row_size = prop.get("row_size", 0)  # 行数
            cell_ids = table_data.get("cells", [])
            
            # 获取所有单元格文本
            cells_text = []
            for cell_id in cell_ids:
                for sub_block in blocks:
                    if sub_block.get("block_id") == cell_id and sub_block.get("block_type") == 32:
                        cell_text = ""
                        cell_children = sub_block.get("children", [])
                        for text_child_id in cell_children:
                            for text_block in blocks:
                                if text_block.get("block_id") == text_child_id and text_block.get("block_type") == 2:
                                    text_data = text_block.get("text", {})
                                    if text_data:
                                        elements = text_data.get("elements", [])
                                        for elem in elements:
                                            cell_text += elem.get("text_run", {}).get("content", "")
                        cells_text.append(cell_text.strip())
            
            # 按列数分组为行
            if cells_text:
                content_lines.append("")
                row_idx = 0
                while row_idx < len(cells_text):
                    row = cells_text[row_idx:row_idx + column_size]
                    if row_idx == 0:
                        # 表头
                        content_lines.append("| " + " | ".join(row) + " |")
                        content_lines.append("| " + " | ".join(["---"] * len(row)) + " |")
                    else:
                        # 数据行
                        content_lines.append("| " + " | ".join(row) + " |")
                    row_idx += column_size
                content_lines.append("")
        
        # Heading block (类型 4=heading2, 5=heading3)
        elif block_type in [4, 5]:
            # 从 heading3/heading2/heading1 字段获取内容
            heading_text = ""
            heading_level = 2  # 默认 level 2 (block_type=4)
            
            # 检查 heading 层级
            if block.get("heading1"):
                heading_data = block["heading1"]
                heading_level = 1
            elif block.get("heading2"):
                heading_data = block["heading2"]
                heading_level = 2
            elif block.get("heading3"):
                heading_data = block["heading3"]
                heading_level = 3
            else:
                heading_data = {}
            
            # 提取文本内容
            if heading_data:
                elements = heading_data.get("elements", [])
                for elem in elements:
                    heading_text += elem.get("text_run", {}).get("content", "")
            
            if heading_text.strip():
                hashes = "#" * heading_level
                content_lines.append(f"{hashes} {heading_text.strip()}")
        
        # 引用 block (类型 15=quote)
        elif block_type == 15:
            quote_data = block.get("quote", {})
            if quote_data:
                elements = quote_data.get("elements", [])
                quote_text = ""
                for elem in elements:
                    quote_text += elem.get("text_run", {}).get("content", "")
                if quote_text.strip():
                    # 用引用格式或粗体显示
                    content_lines.append(f"**{quote_text.strip()}**")
        
        # 列表 block (类型 13=ordered/bullet, 14=bullet, 16=todo)
        elif block_type in [13, 14, 16]:
            # 从 ordered/bullet 字段获取内容
            list_text = ""
            
            # 检查 ordered 字段（可能是有序列表或带样式的列表）
            if block.get("ordered"):
                elements = block["ordered"].get("elements", [])
                for elem in elements:
                    list_text += elem.get("text_run", {}).get("content", "")
            elif block.get("bullet"):
                elements = block["bullet"].get("elements", [])
                for elem in elements:
                    list_text += elem.get("text_run", {}).get("content", "")
            elif block.get("todo"):
                elements = block["todo"].get("elements", [])
                for elem in elements:
                    list_text += elem.get("text_run", {}).get("content", "")
            
            if list_text.strip():
                # 列表前加空行（Markdown 要求）
                if content_lines and content_lines[-1].strip():
                    content_lines.append("")
                content_lines.append(f"- {list_text.strip()}")
        
        # 文本 block (类型 1=Page, 2=Text, 12=Bullet, 22=divider)
        elif block_type in [1, 2, 12, 22]:
            # 跳过表格内的文本 block
            if block.get("block_id") in table_text_ids:
                continue
            
            # block_type=22 是分隔符，跳过
            if block_type == 22:
                continue
            
            # 检查是否有 bullet 字段
            if block_type == 12 and block.get("bullet"):
                bullet_data = block["bullet"]
                elements = bullet_data.get("elements", [])
                bullet_text = ""
                for elem in elements:
                    bullet_text += elem.get("text_run", {}).get("content", "")
                if bullet_text.strip():
                    # 列表前加空行（Markdown 要求）
                    if content_lines and content_lines[-1].strip():
                        content_lines.append("")
                    content_lines.append(f"- {bullet_text.strip()}")
            # 检查是否有 text 字段
            elif block.get("text"):
                text_data = block["text"]
                elements = text_data.get("elements", [])
                block_text = ""
                for elem in elements:
                    text_run = elem.get("text_run", {})
                    block_text += text_run.get("content", "")
                
                if block_text.strip():
                    content_lines.append(block_text)
        
        # 图片 block (类型 27)
        elif block_type == 27:
            image_info = block.get("image", {})
            token = image_info.get("token")
            if token:
                image_count += 1
                img_path = output_dir / f"image_{image_count}.jpg"  # 使用 JPG 格式
                if download_feishu_image(token, token_header, img_path, feishu_host):
                    print(f"  ✅ 下载图片{image_count}: {img_path.name}")
                    # 在当前位置插入图片引用（使用绝对路径）
                    # 图片前加空行
                    if content_lines and content_lines[-1].strip():
                        content_lines.append("")
                    content_lines.append(f"![图{image_count}]({str(img_path)})")
                    content_lines.append("")  # 图片后加空行
    
    content = "\n".join(content_lines)
    # 后处理：确保 Markdown 格式规范
    content = postprocess_markdown(content)
    return content

def download_feishu_image(token, token_header, output_path, feishu_host=None):
    """下载飞书图片（保存为 JPG 格式，微信支持）"""
    # 尝试多个 API endpoint
    urls = [
        f"https://open.feishu.cn/open-apis/drive/v1/media/{token}/download",
        f"https://open.feishu.cn/open-apis/drive/v1/medias/{token}/download",
        f"https://open.feishu.cn/open-apis/drive/v1/files/{token}/download",
    ]
    
    headers = {"Authorization": token_header}
    image_data = None
    
    for url in urls:
        resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 0:
            image_data = resp.content
            break
    
    # 如果都失败，尝试直接访问飞书 CDN
    if not image_data and feishu_host:
        cdn_url = f"https://{feishu_host}/api/box/stream/download/all?fileToken={token}"
        resp = requests.get(cdn_url, headers=headers, timeout=30, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 0:
            image_data = resp.content
    
    if not image_data:
        print(f"⚠️ 下载图片失败：所有 endpoint 都返回空")
        return False
    
    # 保存为 JPG 格式（微信支持 JPG，不支持 PNG）
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_data))
        # 转换为 RGB（处理 PNG 透明通道）
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        # 保存为 JPG
        output_path_jpg = output_path.with_suffix('.jpg')
        img.save(output_path_jpg, 'JPEG', quality=90)
        print(f"  ✅ 下载并转换：{output_path_jpg.name}")
        return True
    except Exception as e:
        # PIL 不可用，直接保存（可能失败）
        print(f"⚠️ PIL 不可用，尝试直接保存：{e}")
        output_path.write_bytes(image_data)
        return True

def select_cover_image(images, output_dir):
    """选择一张图片作为封面"""
    if not images:
        return None
    
    print(f"🖼️  共发现 {len(images)} 张图片，选择封面...")
    
    # 策略：选择第一张宽高比接近 16:9 或 2.35:1 的图片
    # 或者选择面积最大的图片
    best_image = None
    best_score = 0
    
    for i, img in enumerate(images):
        width = img.get("width", 0)
        height = img.get("height", 0)
        if width == 0 or height == 0:
            continue
        
        aspect_ratio = width / height
        area = width * height
        
        # 理想封面比例：16:9 (1.78) 或 2.35:1
        # 越接近理想比例，分数越高
        ratio_score = 1 / (1 + abs(aspect_ratio - 1.78))
        area_score = min(area / 1000000, 1)  # 归一化面积分数
        
        total_score = ratio_score * 0.6 + area_score * 0.4
        
        if total_score > best_score:
            best_score = total_score
            best_image = img
            best_index = i
    
    if best_image:
        # 下载封面图片
        cover_path = output_dir / "cover.png"
        print(f"📸 选择第 {best_index + 1} 张图片作为封面 ({best_image['width']}x{best_image['height']})")
        
        # 需要从 doc_data 获取 token
        return best_image
    
    # 默认返回第一张
    return images[0] if images else None

def is_paper_title(title):
    """判断标题是否是论文标题"""
    if not title or len(title) < 5:
        return False
    title_lower = title.lower()
    match_count = sum(1 for kw in PAPER_TITLE_KEYWORDS if kw.lower() in title_lower)
    return match_count >= 2

def clean_title_for_wechat(title):
    """清理标题，移除微信不支持的表情和特殊字符，智能精简"""
    if not title:
        return ""
    
    import re
    
    # 移除常见 emoji
    title = re.sub(r'[\U0001F300-\U0001F9FF]', '', title)
    # 移除其他特殊符号
    title = re.sub(r'[🎉🔥⭐💫🌟✨⚡❤️💔✅❌⚠️]', '', title)
    # 清理多余空格和分隔符
    title = re.sub(r'^[\s|—-]+', '', title)
    title = re.sub(r'[\s|—-]+$', '', title)
    title = re.sub(r'\s+', ' ', title)
    
    # 微信标题限制 64 字
    if len(title) > 64:
        # 智能精简策略
        # 1. 移除冗余词
        title = title.replace('前沿', '')
        title = title.replace('团队', '')
        title = title.replace('提出', '')
        title = title.replace('用', '')
        title = title.replace('的', '')
        title = title.replace('难题，', '，')
        
        # 2. 如果还是太长，在合适的位置截断（逗号、冒号处）
        if len(title) > 64:
            # 优先在逗号处截断
            for sep in ['，', ',', '：', ':', '|']:
                parts = title.split(sep)
                result = []
                total_len = 0
                for part in parts:
                    if total_len + len(part) + 1 <= 64:
                        result.append(part)
                        total_len += len(part) + 1
                    else:
                        break
                if result:
                    title = sep.join(result).strip()
                    break
            
            # 如果还是太长，直接截断但确保语句完整
            if len(title) > 64:
                # 找到最后一个完整的词
                cut_point = 60
                while cut_point > 0 and not title[cut_point].isalnum() and not re.match(r'[\u4e00-\u9fff]', title[cut_point]):
                    cut_point -= 1
                title = title[:cut_point].strip()
    
    return title.strip()

def extract_title_from_content(content):
    """从内容中提取/生成标题"""
    lines = content.split('\n')
    
    # 最优匹配：包含 "GigaAI" + "提出" + "DriveDreamer" 的行
    for line in lines[:15]:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('!['):
            continue
        if 'GigaAI' in line and '提出' in line and 'DriveDreamer' in line:
            line = re.sub(r'^[🚀📊💡🧠👁️🛤️✨]\s*', '', line)
            # 提取 "提出" 之前的部分作为标题
            if '提出' in line:
                parts = line.split('提出', 1)
                prefix = parts[0].strip()
                if len(prefix) > 10:
                    return f"{prefix}提出 DriveDreamer-Policy"
            return line[:60]
    
    # 次优匹配：包含 "DriveDreamer-Policy" 且有冒号的行
    for line in lines[:15]:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('!['):
            continue
        if 'DriveDreamer-Policy' in line and ':' in line:
            line = re.sub(r'^[🚀📊💡🧠👁️🛤️✨]\s*', '', line)
            parts = line.split(':', 1)
            main_title = parts[0].strip()
            if 15 < len(main_title) < 60:
                return main_title
    
    # 搜索包含模型名称的行
    for line in lines[:15]:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('!['):
            continue
        if len(line) > 30 and len(line) < 100:
            if any(kw in line for kw in ['DriveDreamer', 'GigaAI', '多伦多大学', '香港中文大学', 'MMLab']):
                line = re.sub(r'^[🚀📊💡🧠👁️🛤️✨]\s*', '', line)
                if ':' in line:
                    parts = line.split(':', 1)
                    if 15 < len(parts[0]) < 60:
                        return parts[0].strip()
                return line[:60]
    
    # 搜索包含论文特征词的行
    paper_keywords = ['论文', '研究', '提出', 'Framework', 'Model', 'arXiv']
    for line in lines[:15]:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('!['):
            continue
        if len(line) > 20 and len(line) < 100:
            if any(kw in line for kw in paper_keywords):
                line = re.sub(r'^[🚀📊💡🧠👁️🛤️✨]\s*', '', line)
                return line
    
    # 默认：使用第一行有意义的文本
    for line in lines[:10]:
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('![') and len(line) > 15:
            return line
    
    return "最新论文解读"
    for line in lines[:10]:
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('![') and len(line) > 15:
            return f"【论文深读】{line}"
    
    return "最新论文解读"

def has_images_in_doc(content):
    """检测文档中是否有图片"""
    image_patterns = [
        r'!\[.*?\]\(.*?\)',
        r'<img.*?src=.*?>',
        r'https?://[\w.-]+\.(png|jpg|jpeg|gif|webp)',
        r'!\[图', r'Figure', r'Fig\.', r'图\s*\d'
    ]
    for pattern in image_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            return True
    return False

def extract_pdf_links(content):
    """从内容中提取 PDF 链接"""
    pdf_patterns = [
        r'https?://[\w.-]+\.feishu\.cn/file/[\w-]+',
        r'https?://[\w.-]+\.feishu\.cn/drive/[\w-]+',
        r'https?://[\w.-]+\.feishu\.cn/[\w.-]+/[\w-]+\.pdf[\w=&-]*'
    ]
    for pattern in pdf_patterns:
        match = re.search(pattern, content)
        if match:
            return match.group(0)
    return None

def download_pdf(pdf_url):
    """下载论文 PDF"""
    print(f"📥 下载 PDF: {pdf_url}")
    PDF_DIR.mkdir(exist_ok=True)
    
    filename = pdf_url.split('/')[-1].split('?')[0]
    if not filename.endswith('.pdf'):
        filename = f"paper_{len(list(PDF_DIR.glob('*.pdf')))}.pdf"
    
    output_path = PDF_DIR / filename
    cmd = ["curl", "-L", "-o", str(output_path), pdf_url]
    result = subprocess.run(cmd, capture_output=True)
    
    if result.returncode == 0:
        print(f"✅ PDF 已下载：{output_path}")
        return str(output_path)
    else:
        print(f"❌ 下载失败：{result.stderr.decode()}")
        return None

def screenshot_pdf(pdf_path, num_pages=5):
    """PDF 截图"""
    print(f"📸 PDF 截图：{pdf_path}")
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    base_name = Path(pdf_path).stem
    screenshots = []
    
    for page in range(1, min(num_pages + 1, 10)):
        output_prefix = str(SCREENSHOT_DIR / f"{base_name}_p{page}")
        cmd = ["pdftoppm", "-png", "-f", str(page), "-l", str(page), 
               "-scale-to", "800", pdf_path, output_prefix]
        result = subprocess.run(cmd, capture_output=True)
        
        if result.returncode == 0:
            png_file = Path(f"{output_prefix}.png")
            if png_file.exists():
                screenshots.append(str(png_file))
                print(f"  ✅ 第{page}页截图完成")
    
    if not screenshots:
        print("⚠️ pdftoppm 不可用，尝试使用 pdf2image")
        try:
            from pdf2image import convert_from_path
            pages = convert_from_path(pdf_path, dpi=150, first_page=1, last_page=num_pages)
            for i, page in enumerate(pages, 1):
                img_path = SCREENSHOT_DIR / f"{base_name}_p{i}.png"
                page.save(img_path, 'PNG')
                screenshots.append(str(img_path))
                print(f"  ✅ 第{i}页截图完成")
        except Exception as e:
            print(f"⚠️ 截图失败：{e}")
    
    return screenshots

def clean_content_for_publish(content):
    """清理内容，移除 PDF 链接等不需要发布的部分"""
    print("🧹 清理内容...")
    
    pdf_patterns = [
        r'https?://[\w.-]+\.feishu\.cn/file/[\w-]+',
        r'https?://[\w.-]+\.feishu\.cn/drive/[\w-]+',
        r'https?://[\w.-]+\.feishu\.cn/[\w.-]+/[\w-]+\.pdf[\w=&-]*',
        r'\n.*PDF.*\n',
        r'\n.*pdf.*\n',
        r'\n.*下载.*\n',
        r'\n.*附件.*\n'
    ]
    
    for pattern in pdf_patterns:
        content = re.sub(pattern, '\n', content, flags=re.IGNORECASE)
    
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content

def format_for_wechat(content, title, author, cover_path, template='viral'):
    """格式化为微信公众号文章"""
    print("📝 格式化内容...")
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    cleaned_content = clean_content_for_publish(content)
    
    # 检查是否已有结束语
    ending_text = "持续关注，我们将带来更多自动驾驶与机器人前沿论文解读"
    has_ending = ending_text in cleaned_content
    
    # 如果没有结束语，添加到文末
    if not has_ending:
        cleaned_content += f"\n\n---\n\n**✨ {ending_text}**\n\n**✨欢迎对越野机器人感兴趣的同行加微信交流：15711463195**"
    
    md_content = f"""# {title}

{cleaned_content}
"""
    
    output_file = OUTPUT_DIR / "article.md"
    output_file.write_text(md_content, encoding='utf-8')
    print(f"✅ Markdown 已保存：{output_file}")
    
    return str(output_file), cover_path

def send_webhook_notification(webhook_url, title, draft_media_id):
    """通过飞书 webhook 发送发布通知"""
    print("📤 发送 webhook 通知...")
    
    # 生成草稿箱链接（需要手动拼接）
    draft_link = "微信公众号后台 - 草稿箱"
    
    message = {
        "msg_type": "text",
        "content": {
            "text": f"✅ 推文已发布成功\n\n📝 标题：{title}\n\n🔗 请登录微信公众号后台查看草稿箱"
        }
    }
    
    try:
        resp = requests.post(webhook_url, json=message, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("code") == 0:
                print("✅ Webhook 通知发送成功")
                return True
            else:
                print(f"⚠️ Webhook 返回错误：{result}")
        else:
            print(f"⚠️ Webhook 请求失败：{resp.status_code}")
    except Exception as e:
        print(f"⚠️ 发送 webhook 失败：{e}")
    
    return False

def publish_to_wechat(md_file, title, cover_path, config, dry_run=False, webhook_url=None):
    """发布到微信公众号"""
    print("📮 发布到微信公众号...")
    
    # 先上传所有本地图片到微信服务器
    upload_script = SKILL_DIR / "scripts" / "upload_images_to_wechat.py"
    if upload_script.exists() and not dry_run:
        print("📸 上传图片到微信服务器...")
        output_dir = Path(md_file).parent
        uploaded_md = output_dir / "article_uploaded.md"
        
        upload_cmd = [
            sys.executable, str(upload_script),
            md_file,
            str(uploaded_md)
        ]
        
        upload_result = subprocess.run(upload_cmd, capture_output=True, text=True)
        if upload_result.returncode == 0:
            print(upload_result.stdout)
            md_file = str(uploaded_md)
        else:
            upload_error = "\n".join(part for part in [upload_result.stdout, upload_result.stderr] if part.strip())
            print(f"⚠️ 图片上传失败：{upload_error}")
            # 继续尝试发布，但图片可能无法显示
    elif dry_run:
        print("🔍 预览模式跳过微信图片上传")
    
    script = SKILL_DIR / "scripts" / "publish_wechat.py"
    
    if not script.exists():
        print("❌ 未找到内置微信公众号发布脚本")
        return None
    
    # 创建临时配置文件（兼容 publish_wechat.py 的格式）
    temp_config = {
        "wechat": {
            "app_id": config['wechat']['app_id'],
            "app_secret": config['wechat']['app_secret']
        },
        "author": config.get('author', 'RobotQu'),
        "template": config.get('template', 'viral')
    }
    temp_config_path = SKILL_DIR / "config_temp.json"
    temp_config_path.write_text(json.dumps(temp_config, indent=2, ensure_ascii=False))
    
    cmd = [
        sys.executable, str(script),
        md_file,
        "--config", str(temp_config_path),
        "--template", config.get('template', 'viral')
    ]
    
    if cover_path and os.path.exists(cover_path):
        cmd.extend(["--cover-image", cover_path])
        print(f"🎨 使用封面：{cover_path}")
    
    if dry_run:
        cmd.append("--dry-run")
        print("🔍 预览模式（不实际发布）")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # 清理临时配置
    if temp_config_path.exists():
        temp_config_path.unlink()
    
    if result.returncode == 0:
        draft_media_id = None
        preview_html = None
        try:
            payload = json.loads(result.stdout.strip())
            draft_media_id = payload.get('draft_media_id') or None
            preview_html = payload.get('preview_html') or None
        except Exception:
            payload = {}

        if dry_run:
            print("✅ 预览生成成功！")
            if preview_html:
                print(f"🔍 预览 HTML：{preview_html}")
            return preview_html or True

        print("✅ 发布成功！")
        for line in result.stdout.split('\n'):
            if 'draft_media_id' in line or 'mp.weixin.qq.com' in line:
                print(f"📋 {line.strip()}")
                if 'draft_media_id' in line and not draft_media_id:
                    try:
                        draft_data = json.loads(line.strip())
                        draft_media_id = draft_data.get('draft_media_id', '')
                    except:
                        pass
        
        # 发送 webhook 通知
        if webhook_url and not dry_run:
            send_webhook_notification(webhook_url, title, draft_media_id)
        
        return draft_media_id or True
    else:
        publish_error = "\n".join(part for part in [result.stdout, result.stderr] if part.strip())
        print(f"❌ 发布失败：{publish_error}")
        return None

def main():
    parser = argparse.ArgumentParser(description="飞书文档自动转公众号推文")
    parser.add_argument("url", help="飞书文档链接")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不发布")
    parser.add_argument("--title", help="自定义标题")
    parser.add_argument("--force-screenshot", action="store_true", help="强制截图")
    parser.add_argument("--author", help="作者名")
    parser.add_argument("--webhook", help="飞书群机器人 webhook URL")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🚀 飞书文档自动转公众号推文")
    print("=" * 60)
    
    # 加载配置并获取当前激活的版本
    raw_config = load_config()
    config, version = get_active_config(raw_config)
    print(f"📦 当前版本：{version.upper()}")
    
    if args.author:
        config['author'] = args.author
    
    # 确定 webhook URL：命令行参数 > 配置文件
    webhook_url = args.webhook
    if not webhook_url:
        webhook_url = config.get('webhook', '')
        if webhook_url:
            print(f"📢 使用 {version} webhook")
    
    doc_token = extract_doc_token(args.url)
    if not doc_token:
        print("❌ 无效的飞书链接")
        return
    
    feishu_host = urlparse(args.url).netloc
    doc_data = read_feishu_doc(doc_token, config, feishu_host)
    if not doc_data:
        print("❌ 读取失败")
        return
    
    content = doc_data.get("text", "")
    doc_title = doc_data.get("title", "")
    
    print(f"\n📋 文档原标题：{doc_title[:60]}..." if len(doc_title) > 60 else f"\n📋 文档原标题：{doc_title}")
    
    # 确定最终标题
    if args.title:
        title = clean_title_for_wechat(args.title)
        print(f"✏️ 使用自定义标题：{title}")
    elif doc_title and len(doc_title.strip()) > 10:
        # 有文档标题，清理后使用
        title = clean_title_for_wechat(doc_title)
        print(f"✅ 使用文档原标题（已清理表情）：{title}")
    else:
        # 没有文档标题，从内容生成
        raw_title = extract_title_from_content(content)
        title = clean_title_for_wechat(raw_title)
        print(f"🎯 从内容生成标题：{title}")
    
    # 统计图片数量
    image_count = content.count('![图')
    if image_count > 0:
        print(f"\n🖼️  发现 {image_count} 张图片（已插入到原文位置）")
        # 获取第一张图片作为封面
        import re
        match = re.search(r'!\[图\d\]\(([^)]+)\)', content)
        if match:
            cover_path = match.group(1)
            print(f"🎨 使用第 1 张图片作为封面：{cover_path}")
    else:
        print("\n⚠️ 未发现图片")
        cover_path = None
    
    # 格式化内容
    md_file, cover_path = format_for_wechat(
        content,
        title,
        config.get('author', 'RobotQu'),
        cover_path,
        config.get('template', 'viral')
    )
    
    # 发布到公众号
    publish_result = publish_to_wechat(md_file, title, cover_path, config, args.dry_run, webhook_url)
    
    print("\n" + "=" * 60)
    print("✅ 处理完成！")
    print("=" * 60)
    print(f"\n📁 输出目录：{OUTPUT_DIR}")
    print(f"📁 图片目录：{IMAGES_DIR}")
    if cover_path:
        print(f"🎨 封面图片：{cover_path}")
    if publish_result:
        if args.dry_run:
            print(f"\n🔍 预览 HTML：{publish_result}")
        else:
            print(f"\n📋 草稿 media_id：{publish_result}")

if __name__ == "__main__":
    main()

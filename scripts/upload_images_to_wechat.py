#!/usr/bin/env python3
"""
上传 Markdown 中的本地图片到微信服务器，并替换为微信 URL
"""

import json
import os
import re
import sys
import time
from pathlib import Path
import requests

# 配置路径
SKILL_DIR = Path(__file__).parent.parent
CONFIG_CANDIDATES = [
    Path(os.environ["FEISHU_WECHAT_CONFIG"]) if os.environ.get("FEISHU_WECHAT_CONFIG") else None,
    SKILL_DIR / "config.json",
    SKILL_DIR.parent / "config.json",
    SKILL_DIR.parent.parent / "config.json" if SKILL_DIR.parent.name == "skills" else None,
]
CONFIG_FILE = next((path for path in CONFIG_CANDIDATES if path and path.exists()), SKILL_DIR / "config.json")

WECHAT_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
WECHAT_UPLOAD_URL = "https://api.weixin.qq.com/cgi-bin/material/add_material"


def load_config():
    """加载配置"""
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"配置文件不存在：{CONFIG_FILE}")
    
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    
    # 获取当前激活的版本
    version = config.get('_comment', '').replace('当前使用版本：', '').strip()
    if not version or version not in ['test', 'production']:
        version = 'test'
    
    active = config.get(version, {})
    wechat_config = active.get('wechat', {})
    
    return {
        'wechat': {
            'app_id': wechat_config.get('app_id', ''),
            'app_secret': wechat_config.get('app_secret', '')
        }
    }


def get_wechat_token(app_id, app_secret):
    """获取微信公众号 access_token"""
    last_error = None
    for attempt in range(1, 7):
        params = {
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": app_secret
        }
        resp = requests.get(WECHAT_TOKEN_URL, params=params, timeout=30)
        data = resp.json()
        if "access_token" in data:
            return data["access_token"]

        last_error = data
        if data.get("errcode") != 40164:
            break
        time.sleep(min(attempt, 3))

    raise RuntimeError(f"获取 token 失败：{last_error}")


def upload_image_to_wechat(token, image_path):
    """上传图片到微信服务器"""
    if not Path(image_path).exists():
        print(f"⚠️ 图片不存在：{image_path}")
        return None
    
    # 检测图片格式
    suffix = Path(image_path).suffix.lower()
    content_type = 'image/jpeg' if suffix == '.jpg' else 'image/png'
    
    with open(image_path, "rb") as f:
        files = {"media": (Path(image_path).name, f, content_type)}
        params = {"access_token": token, "type": "image"}
        resp = requests.post(WECHAT_UPLOAD_URL, params=params, files=files, timeout=60)
    
    data = resp.json()
    if data.get("errcode", 0) != 0:
        print(f"⚠️ 上传失败：{data}")
        return None
    
    media_id = data.get("media_id")
    url = data.get("url")  # 微信返回的图片 URL
    print(f"✅ 已上传：{Path(image_path).name} -> {url}")
    return url


def process_markdown(md_path, output_path=None):
    """处理 Markdown 文件，上传所有本地图片并替换为微信 URL"""
    md_path = Path(md_path)
    if not md_path.exists():
        raise RuntimeError(f"文件不存在：{md_path}")
    
    if output_path is None:
        output_path = md_path.with_name(md_path.stem + "_uploaded.md")
    else:
        output_path = Path(output_path)
    
    # 加载配置
    config = load_config()
    wechat_config = config.get("wechat", {})
    app_id = wechat_config.get("app_id", "")
    app_secret = wechat_config.get("app_secret", "")
    
    if not app_id or not app_secret:
        raise RuntimeError("配置缺少 wechat.app_id 或 wechat.app_secret")
    
    print(f"🔑 获取微信公众号 token...")
    token = get_wechat_token(app_id, app_secret)
    
    # 读取 Markdown 内容
    content = md_path.read_text(encoding="utf-8")
    
    # 提取所有本地图片路径
    # 匹配格式：![alt](path) 或 <img src="path">
    image_pattern = r'!\[[^\]]*\]\(([^)]+)\)'
    matches = re.findall(image_pattern, content)
    
    # 也匹配 HTML 图片标签
    html_img_pattern = r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>'
    html_matches = re.findall(html_img_pattern, content)
    matches.extend(html_matches)
    
    # 过滤出本地路径（以 / 开头或相对路径）
    local_images = []
    for img_path in matches:
        img_path = img_path.strip()
        if img_path.startswith(('http://', 'https://')):
            continue  # 网络图片跳过
        if img_path.startswith('/'):
            local_images.append(img_path)
        elif not img_path.startswith('data:'):
            # 相对路径，相对于 Markdown 文件所在目录
            local_images.append(str(md_path.parent / img_path))
    
    print(f"📸 发现 {len(local_images)} 张本地图片")
    
    # 上传所有本地图片
    image_urls = {}
    for img_path in local_images:
        if img_path not in image_urls:
            url = upload_image_to_wechat(token, img_path)
            if url:
                image_urls[img_path] = url
    
    # 替换 Markdown 中的图片路径
    for img_path, wechat_url in image_urls.items():
        # 转义路径中的特殊字符
        escaped_path = re.escape(img_path)
        # 替换 Markdown 格式
        content = re.sub(
            r'!\[([^\]]*)\]\(' + escaped_path + r'\)',
            f'![\\1]({wechat_url})',
            content
        )
        # 替换 HTML 格式
        content = re.sub(
            r'<img([^>]*)src=["\']' + escaped_path + r'["\']([^>]*)>',
            f'<img\\1src="{wechat_url}"\\2>',
            content
        )
    
    # 保存处理后的文件
    output_path.write_text(content, encoding="utf-8")
    print(f"✅ 已保存：{output_path}")
    print(f"📊 共上传 {len(image_urls)} 张图片")
    
    return str(output_path), len(image_urls)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python upload_images_to_wechat.py <markdown 文件> [输出文件]")
        sys.exit(1)
    
    md_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        output, count = process_markdown(md_file, output_file)
        print(f"\n✅ 处理完成！上传了 {count} 张图片")
        print(f"📁 输出文件：{output}")
    except Exception as e:
        print(f"❌ 错误：{e}")
        sys.exit(1)

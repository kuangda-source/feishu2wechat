#!/usr/bin/env python3
"""微信发布器 viral 模板排版回归测试。"""

from publish_wechat import markdown_to_html, optimize_for_wechat_html


def render(markdown_text: str) -> str:
    return optimize_for_wechat_html(markdown_to_html(markdown_text), template="viral")


def test_viral_template_keeps_markdown_semantics():
    html = render(
        """# 标题

## 方法框架

**👉 直接预测可通行性**

### 多模态融合

- 摄像头
- 激光雷达

![图1](/tmp/image_1.jpg)

| 指标 | 结果 |
| --- | --- |
| MSE | 0.12 |

---
"""
    )

    assert "<ul" in html
    assert "<li" in html
    assert "核心判断" in html
    assert "SECTION 01" in html
    assert "Songti SC" in html
    assert 'src="/tmp/image_1.jpg"' in html
    assert "<table" in html
    assert "background:#0f766e" in html
    assert "border-top:3px solid #cbd5e1" in html


def test_chinese_enum_lines_become_dotted_paragraphs():
    html = render(
        """# 标题

## 章节

这里有几个典型难点：
第一，边界不清晰。
第二，视觉容易歧义。
第三类是查询坐标。
后续普通正文。
"""
    )

    assert html.count('data-enum-point="true"') == 3
    assert "·</span><span>第一，边界不清晰。" in html
    assert "后续普通正文。" in html


if __name__ == "__main__":
    test_viral_template_keeps_markdown_semantics()
    test_chinese_enum_lines_become_dotted_paragraphs()
    print("publish_wechat format tests passed")

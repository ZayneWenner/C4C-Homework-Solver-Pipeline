#!/usr/bin/env python3
"""
Stage 1: Document Ingestion
读取作业文件（Markdown / PDF / Word / 图片），输出结构化文本。

Starter kit 仅实现 Markdown 读取。
PDF / Word / OCR 留给学生扩展（见 C4C.md Level 2）。

用法:
    python ingest.py input_file output.json
    python ingest.py homework.md problems.json
"""

import json
import re
import sys
from pathlib import Path


# ─────────────────────────────────────────────
# 核心函数：读取不同格式
# ─────────────────────────────────────────────

def read_markdown(filepath: str) -> str:
    """读取 Markdown 文件，返回原始文本。"""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def read_pdf_text(filepath: str) -> str:
    """
    读取文本型 PDF。

    【C4C Level 2 实现】baseline 这里是 NotImplementedError。
    用 pdfplumber 逐页提取；若某页几乎无文本，判定为扫描页并尝试 OCR。
    """
    try:
        import pdfplumber
    except ImportError:
        raise NotImplementedError(
            "PDF 摄入需要 pdfplumber：pip install pdfplumber"
        )

    pages = []
    with pdfplumber.open(filepath) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            txt = page.extract_text() or ""
            if len(txt.strip()) < 20:
                # 扫描页：尽量 OCR，失败也不断，标注占位
                txt = _try_ocr_page(page) or ""
            pages.append(txt)
    return "\n\n".join(pages)


def _try_ocr_page(page) -> str:
    """扫描页兜底：调 pytesseract；不可用时返回空（上层会保留原样）。"""
    try:
        import pytesseract
        from PIL import Image
        img = page.to_image(resolution=300).original
        return pytesseract.image_to_string(img, lang="chi_sim+eng")
    except Exception:
        return ""


def read_docx(filepath: str) -> str:
    """
    读取 Word 文档。

    【C4C Level 2 实现】baseline 这里是 NotImplementedError。
    段落 + 表格单元格都提取；Word 里的公式是 OMML，python-docx 读不到，
    故额外扫描 w:object / oMath 并保留提示，避免静默丢题。
    """
    try:
        import docx
    except ImportError:
        raise NotImplementedError(
            "Word 摄入需要 python-docx：pip install python-docx"
        )

    doc = docx.Document(filepath)
    lines = []

    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            lines.append(t)

    # 表格：按行读出，用 | 分隔单元格
    for ti, table in enumerate(doc.tables, 1):
        lines.append(f"\n[表格 {ti}]")
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            lines.append(" | ".join(cells))

    # 公式占位提示：docx 的公式是 OMML XML，纯 python-docx 提取不到文字
    try:
        xml = doc.element.xml
        n_math = xml.count("<m:oMath") + xml.count("<w:object")
        if n_math:
            lines.append(f"\n[注意] 文档中有 {n_math} 处嵌入公式/对象，"
                         f"python-docx 无法读取其 LaTeX 内容；"
                         f"建议改用 PDF 或 Markdown 提交该类作业。")
    except Exception:
        pass

    return "\n".join(lines)


def read_image_ocr(filepath: str) -> str:
    """
    读取图片 / 扫描件。

    【C4C Level 3 实现】三条路径依次尝试：
      1) pytesseract 本地 OCR（免费、离线）
      2) 国产模型 Vision（DeepSeek 当前无视觉接口，预留；Qwen-VL 可用）
      3) 都不可用 → 明确报错，而不是返回空字符串假装成功
    """
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(filepath), lang="chi_sim+eng")
    except Exception:
        pass

    # Vision 通道（需要支持多模态的 provider）
    try:
        from llm_provider import get_provider
        import base64
        from pathlib import Path as _P
        p = get_provider()
        if getattr(p, "supports_vision", False):
            img_b64 = base64.b64encode(_P(filepath).read_bytes()).decode("utf-8")
            return p.vision_to_text(img_b64)
    except Exception:
        pass

    raise NotImplementedError(
        "图片 OCR 不可用。请安装 tesseract 并 pip install pytesseract，\n"
        "或把图片转成 Markdown / 文本型 PDF 后重试。\n"
        "（提示：数学公式的 OCR 效果很差，建议优先用可复制文本的作业源。）"
    )


# ─────────────────────────────────────────────
# 格式检测与路由
# ─────────────────────────────────────────────

FORMAT_HANDLERS = {
    ".md":   read_markdown,
    ".txt":  read_markdown,       # 纯文本同 Markdown 处理
    ".pdf":  read_pdf_text,
    ".docx": read_docx,
    ".png":  read_image_ocr,
    ".jpg":  read_image_ocr,
    ".jpeg": read_image_ocr,
}


def detect_format(filepath: str) -> str:
    """根据扩展名检测文件格式。"""
    ext = Path(filepath).suffix.lower()
    if ext not in FORMAT_HANDLERS:
        raise ValueError(f"不支持的文件格式: {ext}\n支持: {list(FORMAT_HANDLERS.keys())}")
    return ext


def ingest(filepath: str) -> dict:
    """
    主入口：读取任意格式的作业文件，返回结构化结果。

    返回:
        {
            "source_file": "homework.md",
            "format": ".md",
            "raw_text": "...",
            "sections": [
                {"title": "Section Title", "content": "..."},
                ...
            ]
        }
    """
    filepath = str(filepath)
    ext = detect_format(filepath)
    handler = FORMAT_HANDLERS[ext]

    raw_text = handler(filepath)

    # 基础分段：按 Markdown 标题或空行分段
    sections = split_into_sections(raw_text)

    return {
        "source_file": Path(filepath).name,
        "format": ext,
        "raw_text": raw_text,
        "sections": sections,
    }


def split_into_sections(text: str) -> list:
    """
    将文本按 Markdown 标题分段。
    如果没有标题，整个文本作为一个 section。
    """
    sections = []
    current_title = "Untitled"
    current_lines = []

    for line in text.split("\n"):
        # 检测 Markdown 标题
        heading_match = re.match(r"^(#{1,4})\s+(.+)", line)
        if heading_match:
            # 保存前一个 section
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    sections.append({
                        "title": current_title,
                        "content": content,
                    })
            current_title = heading_match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 保存最后一个 section
    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append({
                "title": current_title,
                "content": content,
            })

    return sections


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("用法: python ingest.py <输入文件> <输出.json>")
        print("示例: python ingest.py homework.md problems.json")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    if not Path(input_path).exists():
        print(f"错误: 文件不存在 — {input_path}")
        sys.exit(1)

    print(f"[Stage 1] 文档摄入: {input_path}")
    result = ingest(input_path)

    # 确保输出目录存在
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  格式: {result['format']}")
    print(f"  分段: {len(result['sections'])} sections")
    print(f"  输出: {output_path}")


if __name__ == "__main__":
    main()

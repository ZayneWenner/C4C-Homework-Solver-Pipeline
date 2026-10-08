#!/usr/bin/env python3
r"""
Stage 4: LaTeX Renderer  +  Stage 5: PDF Compiler
=====================================================================

相对 Claude baseline 的改动
--------------------------------------------------------------------
1. **中文支持**
   baseline 的 preamble 是纯英文配置（inputenc + T1 fontenc），
   处理中文 PDF 时会直接报 Unicode 错误。这里按文档语言自动切到
   XeLaTeX + ctex（\usepackage[UTF8]{ctex}），并能落到 Windows MiKTeX。

2. **自动探测 LaTeX 引擎**
   baseline 直接 subprocess.run(["xelatex", ...])，依赖引擎在 PATH 上。
   Windows 上 MiKTeX 默认不进 PATH，于是 Stage 5 必然失败、用户只能手工
   传 Overleaf。这里加了候选目录扫描（含 MiKTeX / TeX Live / MacTeX），
   实现真正的"一条命令出 PDF"。

3. **渲染验证结果**
   Stage 3.5 的 verification 结果直接印在 PDF 里，让用户知道每题的
   答案有没有被数学恒等式验证过 —— 这是 baseline 完全没有的信息层。

4. **长推导不溢出**
   补 \\allowdisplaybreaks，避免矩阵推导段落整块漂到页外。

用法:
    python render_latex.py solutions.json output.tex [--compile] [--lang zh]
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════
# Stage 5 准备：LaTeX 引擎探测
# ═══════════════════════════════════════════════════════════════════

def find_latex_engine(prefer: str = "xelatex") -> tuple:
    """
    在系统和常见安装路径中查找 LaTeX 引擎。

    Windows 上 MiKTeX / TeX Live 通常不在 PATH 里，这是 baseline 的 Stage 5
    在本机必然失败的原因。这里显式扫描候选目录。

    返回 (engine_abs_path_or_name, engine_name) 或 (None, "")
    """
    candidates = [prefer, "pdflatex", "lualatex"]
    for c in ["pdflatex", "xelatex", "lualatex"]:
        if c not in candidates:
            candidates.append(c)

    # 1) 已在 PATH 上
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p, c

    # 2) 扫描常见安装位置（Windows 优先）
    local = os.getenv("LOCALAPPDATA", "")
    home = str(Path.home())
    roots = [
        rf"{local}\Programs\MiKTeX\miktex\bin\x64",
        r"C:\Program Files\MiKTeX\miktex\bin\x64",
        r"C:\texlive\2024\bin\windows",
        r"C:\texlive\2023\bin\windows",
        rf"{home}\AppData\Local\Programs\MiKTeX\miktex\bin\x64",
        "/Library/TeX/texbin",
        "/usr/texbin",
        "/usr/local/texlive/2024/bin/x86_64-linux",
        "/usr/bin",
    ]
    exts = [".exe"] if sys.platform == "win32" else [""]
    for d in roots:
        if not d or not Path(d).exists():
            continue
        for c in candidates:
            for ext in exts:
                cand = Path(d) / f"{c}{ext}"
                if cand.exists():
                    return str(cand), c
    return None, ""


def detect_lang(payload) -> str:
    """按中文字符占比判断文档语言。"""
    sample = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    n_cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return "zh" if n_cjk > max(1, len(sample) * 0.03) else "en"


# ═══════════════════════════════════════════════════════════════════
# LaTeX 模板
# ═══════════════════════════════════════════════════════════════════

def _build_preamble(course: str, student: str, title: str, date_str: str,
                    lang: str = "en") -> str:
    """构建 preamble。中文走 ctex + XeLaTeX，英文保持 baseline 的配置。"""
    common = [
        "\\usepackage{amsmath, amssymb, amsthm}",
        "\\usepackage{geometry}",
        "\\usepackage{fancyhdr}",
        "\\usepackage{enumitem}",
        "\\usepackage{xcolor}",
        "\\usepackage{hyperref}",
        "\\allowdisplaybreaks[4]",          # 允许长推导跨页，避免溢出
    ]

    if lang == "zh":
        head = [
            "\\documentclass[12pt, a4paper]{article}",
            "\\usepackage[UTF8]{ctex}",        # 中文支持（自动挑选中文字体）
        ]
    else:
        head = [
            "\\documentclass[12pt, a4paper]{article}",
            "\\usepackage[utf8]{inputenc}",
            "\\usepackage[T1]{fontenc}",
        ]

    body = [
        "\\geometry{margin=1in}",
        "\\pagestyle{fancy}",
        "\\fancyhf{}",
        f"\\rhead{{{course}}}",
        f"\\lhead{{{student}}}",
        "\\rfoot{Page \\thepage}",
        "",
        "\\newcommand{\\problem}[1]{\\subsection*{Problem #1}}",
        "\\newcommand{\\solution}{\\paragraph{Solution.}}",
        "",
        "\\definecolor{solutioncolor}{RGB}{0, 100, 0}",
        "\\definecolor{warncolor}{RGB}{180, 60, 0}",
        "",
        "\\begin{document}",
        "",
        "\\begin{center}",
        f"    {{\\LARGE\\bfseries {title}}} \\\\[0.5em]",
        f"    {{\\large {course}}} \\\\[0.3em]",
        f"    {student} \\quad | \\quad {date_str}",
        "\\end{center}",
        "",
        "\\hrule",
        "\\vspace{1em}",
    ]

    return "\n".join(head + common + body) + "\n"


LATEX_POSTAMBLE = "\n\\end{document}\n"


# ═══════════════════════════════════════════════════════════════════
# 渲染函数
# ═══════════════════════════════════════════════════════════════════

def render_verification(v: dict) -> str:
    """把 Stage 3.5 的验证结果渲染成 LaTeX 片段。"""
    if not v:
        return ""
    status = v.get("status", "unchecked")
    if status == "verified":
        color = "solutioncolor"
        word = "答案已通过数学恒等式自检"
    elif status == "partially_verified":
        color = "warncolor"
        word = "部分验证通过 \\textbf{使用前请复核}"
    elif status in ("failed", "error"):
        color = "red"
        word = "验证未通过 \\textbf{请勿直接抄用}"
    else:
        color = "gray"
        word = "此题型暂无可执行验证律"

    line = (f"\\noindent{{\\color{{{color}}}\\small "
            f"$\\blacksquare$ {word}（{v.get('passed', 0)}/{v.get('total', 0)}）}}")

    failed = [c for c in v.get("checks", []) if not c["passed"] and c.get("detail")]
    for c in failed[:2]:
        detail = escape_latex(c["detail"][:110])
        line += f"\\newline{{\\color{{{color}}}\\scriptsize {detail}}}"
    return line


def render_problem(solution: dict) -> str:
    """将单个题目+解答渲染为 LaTeX 片段。"""
    lines = []
    pid = solution["problem_id"]

    lines.append(f"\\problem{{{pid}}}")
    lines.append("")

    problem_text = clean_for_latex(solution.get("problem_text", ""))
    lines.append(problem_text)
    lines.append("")

    if solution.get("solved"):
        lines.append("\\solution")
        lines.append("")

        method = solution.get("method")
        if method:
            lines.append(f"\\noindent{{\\small\\textbf{{方法：}}{escape_latex(str(method))}}}")
            lines.append("")

        for step in solution.get("steps", []):
            lines.append(clean_for_latex(step))
            lines.append("")

        answer_latex = solution.get("answer_latex", "")
        if answer_latex:
            lines.append("\\textbf{Answer:}")
            lines.append(f"\\[{answer_latex}\\]")
            lines.append("")

        solver = solution.get("solver", "")
        if solver and str(solver).startswith("llm:"):
            lines.append(f"\\noindent{{\\small\\textit{{由 {escape_latex(str(solver))} 推理生成}}}}")
            lines.append("")

        v = solution.get("verification")
        if v:
            lines.append(render_verification(v))
            lines.append("")
    else:
        reason = solution.get("reason", "自动求解器未能处理此题")
        lines.append(f"\\textit{{\\color{{red}} 未求解: {escape_latex(str(reason))}}}")
        lines.append("")

    for sub_sol in solution.get("sub_solutions", []):
        lines.append(render_sub_problem(sub_sol))

    lines.append("\\vspace{1em}")
    lines.append("\\hrule")
    lines.append("\\vspace{1em}")
    lines.append("")

    return "\n".join(lines)


def render_sub_problem(sub_solution: dict) -> str:
    """渲染子题。"""
    lines = []
    sid = sub_solution["problem_id"]
    parts = str(sid).split(".")
    sub_label = parts[-1] if len(parts) > 1 else sid

    lines.append(f"\\textbf{{({sub_label})}}")
    lines.append(escape_latex(sub_solution.get("problem_text", "")))

    if sub_solution.get("solved"):
        for step in sub_solution.get("steps", []):
            lines.append(clean_for_latex(step))
        answer_latex = sub_solution.get("answer_latex", "")
        if answer_latex:
            lines.append(f"$\\boxed{{{answer_latex}}}$")
    else:
        reason = sub_solution.get("reason", "未求解")
        lines.append(f"\\textit{{\\color{{red}} {escape_latex(str(reason))}}}")

    lines.append("")
    return "\n".join(lines)


def render_document(
    solutions: list,
    course: str = "Course Name",
    student: str = "Student Name",
    title: str = "Homework",
    date_str: str = None,
    lang: str = None,
) -> str:
    """
    将所有解答渲染为完整的 LaTeX 文档。

    语言兜底：调用方未指定 lang 时按内容自动检测。
    """
    if date_str is None:
        date_str = date.today().isoformat()
    if lang is None:
        lang = detect_lang(solutions)

    preamble = _build_preamble(
        course=escape_latex(course),
        student=escape_latex(student),
        title=escape_latex(title),
        date_str=escape_latex(date_str),
        lang=lang,
    )

    body_parts = [render_problem(sol) for sol in solutions]

    total = len(solutions)
    solved = sum(1 for s in solutions if s.get("solved"))
    verified = sum(1 for s in solutions
                   if (s.get("verification") or {}).get("verified"))
    unchecked = sum(1 for s in solutions
                    if s.get("solved") and not (s.get("verification") or {}).get("total"))

    if lang == "zh":
        summary = (f"共 {total} 题，自动求解 {solved} 题，"
                   f"其中 {verified} 题经数学恒等式验证通过，"
                   f"{unchecked} 题暂无适用验证律。")
    else:
        summary = (f"{solved}/{total} problems solved, "
                   f"{verified} verified by mathematical identities, "
                   f"{unchecked} without applicable checks.")

    body_parts.append("\\vspace{2em}")
    body_parts.append(f"\\noindent\\textit{{{summary}}}")

    return preamble + "\n".join(body_parts) + LATEX_POSTAMBLE


# ═══════════════════════════════════════════════════════════════════
# LaTeX 转义工具（沿用 baseline，补强括号/符号）
# ═══════════════════════════════════════════════════════════════════

def escape_latex(text: str) -> str:
    """转义 LaTeX 特殊字符（在非数学环境中）。"""
    text = str(text)
    special = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for char, replacement in special.items():
        text = text.replace(char, replacement)
    return text


_MATH_WARNED: set[str] = set()


def _audit_dollars(text: str) -> None:
    """
    $ 必须成对出现。

    奇数个 $ 的后果很隐蔽且很贵：配对错位后，本该是数学的内容会被当成普通文本，
    花括号被转义成 \\begin\\{matrix\\}，xelatex 一次能报上百个
    "Missing \\endcsname inserted"，而 PDF 还可能照样生成，看起来一切正常。

    与其等编译日志，不如在这里就喊出来。
    """
    n = str(text).count("$")
    if n % 2 == 1:
        key = str(text)[:70]
        if key not in _MATH_WARNED:
            _MATH_WARNED.add(key)
            print(f"   ⚠️ [排版] $ 未成对（{n} 个），该段数学可能被转义：{key}…")


def _apply_bold(seg: str) -> str:
    """
    Markdown 的 **加粗** → \\textbf{}。

    ⚠️ 顺序很讲究：escape_latex 会把 { } 转义成 \\{ \\}，
    所以不能直接"替换成 \\textbf{...} 再 escape"（花括号会被吃掉），
    也不能"先 escape 再替换"（此时 ** 还在，但内部文字已转义，勉强可行却不干净）。
    正确做法：先把加粗段提取成占位符 → escape 全段 → 再回填 \\textbf{}。
    否则 PDF 里会出现字面量的 ** 通解 ** 这种 Markdown 残留。
    """
    store: list[str] = []

    def _sub(m):
        store.append(r"\textbf{" + escape_latex(m.group(1)) + "}")
        return f"\x00B{len(store) - 1}\x00"

    seg = re.sub(r"\*\*(.+?)\*\*", _sub, seg, flags=re.DOTALL)
    seg = escape_latex(seg)
    for i, repl in enumerate(store):
        seg = seg.replace(f"\x00B{i}\x00", repl)
    return seg


def clean_for_latex(text: str) -> str:
    """
    清理文本以适应 LaTeX。
    保留 $...$、$$...$$、\\[...\\] 中的数学内容不转义；
    其余部分做 LaTeX 转义，并把 **加粗** 渲染成真正的粗体。
    """
    if not text:
        return ""

    _audit_dollars(text)

    if re.match(r"^\s*\\\[.*\\\]\s*$", str(text), re.DOTALL):
        return str(text)

    parts = re.split(r"(\$\$.*?\$\$|\\\[.*?\\\]|\$.*?\$)", str(text), flags=re.DOTALL)
    cleaned = []
    for part in parts:
        if part.startswith("$") or part.startswith("\\["):
            cleaned.append(part)
        else:
            cleaned.append(_apply_bold(part))
    return "".join(cleaned)


# ═══════════════════════════════════════════════════════════════════
# Stage 5: PDF 编译
# ═══════════════════════════════════════════════════════════════════

def compile_pdf(tex_path: str, output_dir: str = None, lang: str = "en",
                passes: int = 3) -> bool:
    """
    编译 LaTeX → PDF。

    相对 baseline 的改进：
      · 用 find_latex_engine() 定位引擎（Windows MiKTeX 不再失败）
      · 中文文档强制走 xelatex
      · 失败时打印编译日志尾部，而不是只说"编译失败"
    """
    tex_path = Path(tex_path)
    if output_dir is None:
        output_dir = str(tex_path.parent)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    prefer = "xelatex" if lang == "zh" else "pdflatex"
    engine_path, engine_name = find_latex_engine(prefer)
    if engine_path is None:
        print("  ⚠️ 未找到 LaTeX 编译器（xelatex/pdflatex）。")
        print("  安装提示:")
        print("    Windows: winget install MiKTeX.MiKTeX")
        print("    macOS:   brew install --cask mactex-no-gui")
        print("    Ubuntu:  sudo apt install texlive-xetex texlive-lang-chinese")
        print("  或把 homework.tex 上传到 overleaf.com 在线编译")
        return False

    print(f"  使用 {engine_name}: {engine_path}")

    pdf_path = Path(output_dir) / (tex_path.stem + ".pdf")

    # ⚠️ 必须先删掉旧 PDF。
    # 曾经这里写成"编译一遍后若 PDF 存在就 break"——但上一轮的 PDF 本来就在，
    # 于是哪怕这一遍编译彻底失败（xelatex 报上百个错），流水线也照样打印"成功"，
    # 交付的是一份陈旧且错误的 PDF。这是本次最难发现的一个 bug：
    # 它不报错、不崩溃，只是安静地给你一个假的成功。
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except Exception:
            pass

    # ⚠️ 必须传 .tex 的绝对路径。
    # 曾经传相对路径并把 cwd 设成 tex 所在目录，于是作业目录名被叠加两次
    # （output_la\output_la\homework.tex），xelatex 报 "I can't find file"，
    # 而整个流程看起来只像是"编译失败"——真正原因藏在日志第二行。
    abs_tex = str(tex_path.resolve())
    abs_out = str(Path(output_dir).resolve())

    last_log = ""
    for i in range(passes):
        cmd = [
            engine_path,
            "-synctex=1",
            f"-output-directory={abs_out}",
            "-interaction=nonstopmode",
            abs_tex,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=240,
                cwd=abs_out,
                encoding="utf-8", errors="ignore",
            )
            last_log = (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired:
            print(f"  ⚠️ 第 {i+1} 遍编译超时")
            return False

        if pdf_path.exists():
            break

    if not pdf_path.exists():
        print("  ❌ PDF 未生成。编译器日志尾部：")
        tail = "\n".join(last_log.strip().splitlines()[-18:])
        print("  " + "\n  ".join(tail.splitlines()))
        return False

    # PDF 存在还不够：还要确认它确实比 .tex 新（防止又拿到陈旧产物）
    if pdf_path.stat().st_mtime < tex_path.stat().st_mtime:
        print("  ⚠️ PDF 比源文件旧，可能未真正重新编译")

    # 统计致命错误。xelatex 在 nonstopmode 下即使报错也可能吐出一个"残缺但能打开"的 PDF，
    # 只看出不出文件会漏掉这种失败。
    log_file = Path(output_dir) / (tex_path.stem + ".log")
    if log_file.exists():
        txt = log_file.read_text(encoding="utf-8", errors="ignore")
        fatal = [ln for ln in txt.splitlines() if ln.startswith("!")]
        if fatal:
            print(f"  ⚠️ 编译日志含 {len(fatal)} 条错误，PDF 可能不完整。首条：{fatal[0][:90]}")
            return False

    size_kb = pdf_path.stat().st_size / 1024
    print(f"  ✅ PDF 生成成功: {pdf_path} ({size_kb:.1f} KB)")
    return True


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 3:
        print("用法: python render_latex.py <solutions.json> <output.tex> [--compile] [--lang zh]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    do_compile = "--compile" in sys.argv
    lang = "zh" if ("--lang" in sys.argv and "zh" in sys.argv) else "en"

    with open(input_path, "r", encoding="utf-8") as f:
        solutions = json.load(f)

    print(f"[Stage 4] LaTeX 生成: {len(solutions)} 道题目")

    latex_doc = render_document(
        solutions,
        course="线性代数 Linear Algebra" if lang == "zh" else "Linear Algebra",
        student="贾静文 Jia Jingwen",
        title="线性代数作业解答" if lang == "zh" else "Homework Solutions",
        lang=lang,
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(latex_doc)
    print(f"  LaTeX 输出: {output_path}")

    if do_compile:
        print("[Stage 5] PDF 编译")
        if not compile_pdf(output_path, lang=lang):
            print("  提示: 先安装 LaTeX 环境，或在 Overleaf 上编译生成的 .tex 文件")

    print("完成!")


if __name__ == "__main__":
    main()

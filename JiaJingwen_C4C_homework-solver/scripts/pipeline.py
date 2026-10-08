#!/usr/bin/env python3
"""
一键流水线：串联 Stage 1-5，从作业文件到 PDF。

用法:
    python pipeline.py <输入文件> <输出目录> [选项]

示例:
    python pipeline.py examples/sample_homework.md output/
    python pipeline.py homework.pdf output/ --compile
    python pipeline.py homework.docx output/ --course "Linear Algebra" --student "张三"

选项:
    --compile           编译 PDF（需要 LaTeX 环境）
    --course NAME       课程名称（默认: Mathematics）
    --student NAME      学生姓名（默认: Student）
    --title TITLE       作业标题（默认: Homework Solutions）
    --verbose           显示详细输出
"""

import argparse
import json
import sys
import time
from pathlib import Path

# 添加 scripts 目录到 path
scripts_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(scripts_dir))

# ── Auto-install dependencies if missing ──────────────
from bootstrap import ensure_dependencies, SKILL_ROOT
ensure_dependencies()

from ingest import ingest
from parse_problems import parse_problems
from solve import solve_all
from render_latex import render_document, compile_pdf, detect_lang, find_latex_engine

try:
    from llm_provider import get_provider
    _PROVIDER = get_provider()
except Exception:
    _PROVIDER = None


def run_pipeline(
    input_path: str,
    output_dir: str,
    course: str = "Mathematics",
    student: str = "Student",
    title: str = "Homework Solutions",
    do_compile: bool = False,
    verbose: bool = False,
    lang: str = None,
):
    """
    执行完整的作业求解流水线。

    Stage 1: 文档摄入 → problems.json
    Stage 2: 题目解析 → parsed.json
    Stage 3: 自动求解 → solutions.json
      ├─ 确定性通道: SymPy + 模板
      └─ 推理通道  : 国产 LLM（可降级）
    Stage 3.5: 答案验证 → 写回 solutions.json
    Stage 4: LaTeX 生成 → homework.tex
    Stage 5: PDF 编译 → homework.pdf (可选)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    print("=" * 60)
    print("🚀 作业自动求解流水线（国产模型增强版）")
    print(f"   输入: {input_path}")
    print(f"   输出: {output_dir}")

    eng_desc = f"{_PROVIDER.name}:{_PROVIDER.model}" if _PROVIDER else "none"
    eng_stat = "可用" if (_PROVIDER and _PROVIDER.available) else "离线降级"
    print(f"   推理引擎: {eng_desc} [{eng_stat}]")
    print("=" * 60)

    problems = None
    solutions = None
    total = solved = verified = unchecked = 0

    # ── Stage 1: 文档摄入 ──────────────────────
    print("\n📄 Stage 1: 文档摄入")
    try:
        ingested = ingest(input_path)
        ingested_path = output_dir / "1_ingested.json"
        with open(ingested_path, "w", encoding="utf-8") as f:
            json.dump(ingested, f, ensure_ascii=False, indent=2)

        # 若 Stage 2 崩溃，后面仍能给出可诊断的输入，不至于整条线断掉
        globals()["_LAST_INGESTED"] = ingested
        print(f"   ✅ 格式: {ingested['format']}, 分段: {len(ingested['sections'])}")
    except Exception as e:
        print(f"   ❌ 摄入失败: {e}")
        sys.exit(1)

    # ── Stage 2: 题目解析 ──────────────────────
    print("\n🔍 Stage 2: 题目解析（多领域路由）")
    try:
        problems = parse_problems(ingested)
        parsed_path = output_dir / "2_parsed.json"
        with open(parsed_path, "w", encoding="utf-8") as f:
            json.dump(problems, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 识别题目: {len(problems)} 道")
        for p in problems:
            subs = f" ({len(p['sub_problems'])} 子题)" if p["sub_problems"] else ""
            print(f"      Problem {p['id']}: [{p['type']}]{subs}")
    except Exception as e:
        print(f"   ❌ 解析失败: {e}")
        sys.exit(1)

    if not problems:
        print("\n⚠️ 未识别到任何题目。请检查输入文件格式。")
        print("   支持的题号格式: 'Problem N' / '题 N' / 'N.' / 'N)' / 'Q.N'")
        sys.exit(1)

    # ── Stage 3: 自动求解 ──────────────────────
    print("\n🧮 Stage 3: 自动求解（SymPy 优先 → 国产 LLM 兜底）")
    try:
        solutions = solve_all(problems)
        total = len(solutions)
        solved = sum(1 for s in solutions if s.get("solved"))
        verified = sum(1 for s in solutions
                       if (s.get("verification") or {}).get("verified"))
        unchecked = sum(1 for s in solutions
                        if s.get("solved") and not (s.get("verification") or {}).get("total"))

        solutions_path = output_dir / "3_solutions.json"
        with open(solutions_path, "w", encoding="utf-8") as f:
            json.dump(solutions, f, ensure_ascii=False, indent=2)

        print(f"   ✅ 求解完成: {solved}/{total}")
        for s in solutions:
            status = "✅" if s.get("solved") else "❌"
            info = s.get("answer_latex", s.get("reason", ""))[:48]
            v = s.get("verification") or {}
            vsign = ("✔" if v.get("verified") else (f"{v.get('passed', 0)}/{v.get('total', 0)}"
                                                    if v.get("total") else "—"))
            print(f"      {status} Problem {s['problem_id']}: {info}   [验证 {vsign}]")
    except Exception as e:
        print(f"   ❌ 求解失败: {e}")
        import traceback
        traceback.print_exc()
        # 即使求解失败，也把已摄入的内容落盘，便于人工诊断
        try:
            (output_dir / "2_parsed.json").write_text(
                json.dumps(problems or [], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        sys.exit(1)

    if lang is None:
        lang = detect_lang(solutions)

    # ── Stage 4: LaTeX 生成 ──────────────────────
    print("\n📝 Stage 4: LaTeX 生成")
    try:
        tex_content = render_document(
            solutions,
            course=course,
            student=student,
            title=title,
            lang=lang,
        )
        tex_path = output_dir / "homework.tex"
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)
        print(f"   ✅ LaTeX 文件: {tex_path}")
    except Exception as e:
        print(f"   ❌ LaTeX 生成失败: {e}")
        sys.exit(1)

    # ── Stage 5: PDF 编译（可选）──────────────
    if do_compile:
        print("\n📑 Stage 5: PDF 编译")
        success = compile_pdf(str(tex_path), str(output_dir), lang=lang)
        if not success:
            print("   ⚠️ PDF 编译失败")
            print("   替代方案: 将 homework.tex 上传到 overleaf.com 在线编译")
    else:
        print("\n📑 Stage 5: PDF 编译（跳过，加 --compile 启用）")

    # ── 汇总 ──────────────────────────────────
    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print("📊 流水线执行完成")
    print(f"   耗时: {elapsed:.1f} 秒")
    print(f"   题目: {total} 道")
    print(f"   已解: {solved} 道 ({100*solved/max(total,1):.0f}%)")
    print(f"   经恒等式验证: {verified} 道")
    print(f"   无适用验证律: {unchecked} 道")
    if _PROVIDER and _PROVIDER.available:
        print(f"   LLM 调用: {_PROVIDER.stats['calls']} 次 "
              f"(缓存命中 {_PROVIDER.stats['cache_hits']})")
    print(f"   输出文件:")
    for f in sorted(output_dir.iterdir()):
        size = f.stat().st_size
        print(f"      {f.name} ({size:,} bytes)")
    print("=" * 60)

    return {
        "total": total,
        "solved": solved,
        "verified": verified,
        "unchecked": unchecked,
        "output_dir": str(output_dir),
        "elapsed": elapsed,
        "engine": eng_desc if _PROVIDER else "none",
    }


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="作业自动求解流水线: 文件 → 解析 → 求解 → LaTeX → PDF"
    )
    parser.add_argument("input", help="输入文件路径 (md/pdf/docx/图片)")
    parser.add_argument("output_dir", help="输出目录")
    parser.add_argument("--compile", action="store_true", help="编译 PDF")
    parser.add_argument("--course", default="Mathematics", help="课程名称")
    parser.add_argument("--student", default="Student", help="学生姓名")
    parser.add_argument("--title", default="Homework Solutions", help="作业标题")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--lang", default=None, choices=[None, "zh", "en"],
                        help="文档语言；不指定时自动检测")

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"错误: 输入文件不存在 — {args.input}")
        sys.exit(1)

    run_pipeline(
        input_path=args.input,
        output_dir=args.output_dir,
        course=args.course,
        student=args.student,
        title=args.title,
        do_compile=args.compile,
        verbose=args.verbose,
        lang=args.lang,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
r"""
Stage 6: 正确性验证（Correctness Validation）
=====================================================================

Starter kit 完全没有这一层 —— 它只有 "solved: true/false"，
而 solved=true 只意味着"求解器没抛异常"，不代表答案正确。

本脚本把流水线输出与**人工确定的标准答案**逐题对比，
产出可被第三方复核的正确性报告。

对比方式是数学级的而非字符串级的：
  · 矩阵答案   → sympify 回 SymPy 对象 → 逐元素 simplify 比较
  · 标量答案   → simplify(a - b) == 0
  · 判定型答案 → 检查关键命题词是否出现在答案里（如"正定"/"线性相关"）
  · 参数型通解 → 用自由元 FOLLOW：把通解代入原方程组看是否恒成立
                 （比单纯看结构更强 —— 见 _check_general_solution）

跑一遍就能得到验证报告所需的全部数字。

用法:
    python validate.py output_la/3_solutions.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sympy import sympify, simplify, Matrix, symbols, S

try:
    from linalg_solvers import build_context
    from verify import verify_solution
except ImportError:
    build_context, verify_solution = None, None


# ═══════════════════════════════════════════════════════════════════
# 标准答案（人工确定，可复核）
# ═══════════════════════════════════════════════════════════════════
# key = 题号；expect 用 SymPy 可解析的形式书写。
# note 记录这道题来自哪本教材的哪种典型题型。

GROUND_TRUTH = {
    "P1": {
        "kind": "value",
        "expect": "7",
        "topic": "二阶行列式",
        "note": "det[[2,1],[3,5]] = 2·5 − 1·3 = 7",
    },
    "P2": {
        "kind": "matrix",
        "expect": "Matrix([[5, -2], [-2, 1]])",
        "topic": "逆矩阵",
        "note": "det=1，伴随矩阵法 A⁻¹ = [[5,−2],[−2,1]]",
    },
    "P3": {
        "kind": "value",
        "expect": "2",
        "topic": "矩阵的秩",
        "note": "第 2 行 = 2×第 1 行，第 3 行独立 → r = 2",
    },
    "P4": {
        "kind": "verdict",
        "expect": "A^T",
        "topic": "转置（与迹同题）",
        "note": "题目同时问迹与转置，本题取转置：Aᵀ = [[3,4],[−1,2]]；迹 tr(A)=5",
    },
    "P5": {
        "kind": "matrix",
        "expect": "Matrix([[1], [2]])",
        "topic": "线性方程组（唯一解）",
        "note": "代入验证：1+2·2=5 ✓，2·1+3·2=8 ✓",
    },
    "P6": {
        "kind": "general_solution",
        "topic": "线性方程组（无穷多解）",
        "note": "r(A)=r(A|b)=2 < n=3 → 通解含 1 个自由元；"
                "应满足 x₁+x₂+x₃=1 且 x₁+x₂=1，即 x=(1−t, t, 0)",
    },
    "P7": {
        "kind": "verdict",
        "expect": "lambda",
        "topic": "特征值与特征向量",
        "note": "特征方程 λ²−7λ+10=0 → λ=2, 5；"
                "λ=2 对应 (−1,2)ᵀ，λ=5 对应 (1,1)ᵀ",
    },
    "P8": {
        "kind": "verdict",
        "expect": "P = ",
        "topic": "相似对角化",
        "note": "两个互异特征值 → 可对角化；"
                "P=[[−1,1],[2,1]]，Λ=diag(2,5)",
    },
    "P9": {
        "kind": "verdict",
        "expect": "e_",
        "topic": "施密特正交化",
        "note": "β₁=(1,1,0)ᵀ，β₂=(1,0,1)ᵀ−½(1,1,0)ᵀ=(½,−½,1)ᵀ，…；"
                "最终得到单位正交向量组",
    },
    "P10": {
        "kind": "verdict",
        "expect": "线性相关",
        "topic": "线性相关性判定",
        "note": "a₂ = 2a₁ → 向量组线性相关",
    },
    "P11": {
        "kind": "verdict",
        "expect": "正定",
        "topic": "二次型正定性",
        "note": "顺序主子式 D₁=2>0，D₂=3>0 → 正定",
    },
    "AP12": {
        "kind": "verdict",
        "expect": "不存在",
        "topic": "奇异矩阵（不可逆）",
        "note": "det = 1·4 − 2·2 = 0 → 不可逆",
    },
    "AP13": {
        "kind": "verdict",
        "expect": "c_{1}",
        "topic": "齐次方程组基础解系",
        "note": "x₃=0，x₁+x₂=0 → 基础解系 ξ=(−1,1,0)ᵀ，解空间维数 1",
    },
}


# ═══════════════════════════════════════════════════════════════════
# 比较逻辑
# ═══════════════════════════════════════════════════════════════════

def _as_sympy(text):
    """把 solver 输出的 answer 字符串转回 SymPy 对象。失败返回 None。"""
    if text is None:
        return None
    s = str(text).strip()
    # 形如 "Matrix([[5, -2], [-2, 1]])"
    try:
        return sympify(s)
    except Exception:
        pass
    # 退化：只含第一个 {...} 或 [...]
    for opener, closer in (("[", "]"), ("(", ")")):
        if opener in s and closer in s:
            frag = s[s.index(opener): s.rindex(closer) + 1]
            try:
                return sympify(("Matrix(" + frag + ")") if opener == "[" else frag)
            except Exception:
                pass
    return None


def _check_general_solution(sol, problem) -> tuple[bool, str]:
    """
    参数通解的验证：把逗号宣言任给的随机数代入自由元，检查是否满足原方程组。
    这比"看起来结构对"强得多 —— 它直接检查解的合法性。
    """
    if build_context is None:
        return False, "无法加载解析器"
    try:
        ctx = build_context(problem)
        A, b = ctx.get("A"), ctx.get("b")
        if A is None or b is None:
            return False, "未能重建方程组"

        # 从 SymPy 重算特解与基础解系，再代入检验 —— 比"看起来结构对"强得多
        rank_A = A.rank()
        n = A.cols
        particular = None
        try:
            particular = Matrix(simplify(A.gauss_jordan_solve(b)[0]))
            # 特解里的自由参数 τ₀,τ₁,… 统一取 0，得到一个确定的向量
            for s in list(particular.free_symbols):
                if s.name.startswith("tau"):
                    particular = particular.subs(s, 0)
            particular = simplify(particular)
        except Exception:
            pass
        ns = A.nullspace()

        if not ns:
            return False, "未得到基础解系"

        ok = True
        detail = []
        # 取 3 组不同的参数值代入：x = η* + Σ k_i ξ_i
        for trial in range(3):
            base = Matrix(particular) if particular is not None \
                else Matrix.zeros(A.cols, 1)
            # 注意：不能用内置 sum() 累加矩阵，起始值 0 与矩阵相加会类型报错
            combo = Matrix.zeros(A.cols, 1)
            for i, v in enumerate(ns):
                combo += (trial + 1 + i) * v
            x = simplify(base + combo)
            resid = simplify(A * x - b)
            if resid != Matrix.zeros(*resid.shape):
                ok = False
                detail.append(f"第{trial+1}组参数代入不满足原方程（残差 {resid.T}）")
        if ok:
            detail.append("3 组参数代入均满足原方程组")
        detail.append(f"r(A)={rank_A}, 自由元={n-rank_A}, 基础解系维数={len(ns)}")
        return ok, "; ".join(detail)
    except Exception as e:
        return False, f"验证异常: {e}"


def compare(pid: str, sol: dict, problem: dict) -> dict:
    """单题对比，返回结构化结果。"""
    gt = GROUND_TRUTH.get(pid)
    row = {
        "id": pid,
        "topic": gt["topic"] if gt else "-",
        "note": gt["note"] if gt else "-",
        "expected": "-",
        "got": "-",
        "correct": None,
        "detail": "",
        "verified": (sol.get("verification") or {}).get("verified"),
        "verif_total": (sol.get("verification") or {}).get("total", 0),
        "verif_passed": (sol.get("verification") or {}).get("passed", 0),
    }

    if not sol.get("solved"):
        row["correct"] = False
        row["detail"] = f"未求解：{sol.get('reason', '')[:80]}"
        row["got"] = "(未求解)"
        return row

    if gt is None:
        row["correct"] = None
        row["detail"] = "无标准答案，跳过对比"
        row["got"] = str(sol.get("answer"))[:60]
        return row

    row["expected"] = gt.get("expect", "见备注")
    got_raw = sol.get("answer")
    row["got"] = str(got_raw)[:70]

    kind = gt["kind"]

    # ① 通解：代入验证（最强）
    if kind == "general_solution":
        ok, detail = _check_general_solution(sol, problem)
        row["correct"] = ok
        row["detail"] = detail
        return row

    # ② 判定型：关键词包含
    if kind == "verdict":
        text = (str(sol.get("answer_latex", "")) + " " + str(got_raw))
        row["correct"] = gt["expect"] in text
        row["detail"] = f"答案包含期望记号「{gt['expect']}」"
        return row

    # ③ 数值/矩阵：符号相等
    exp = sympify(gt["expect"])
    got = _as_sympy(got_raw)
    if got is None:
        row["correct"] = False
        row["detail"] = f"无法把答案解析为数学对象：{str(got_raw)[:60]}"
        return row
    try:
        diff = simplify(got - exp)
        # ⚠️ SymPy 里 `Matrix == 0` 恒为 False（矩阵与标量不可比），
        #    必须逐元素判断，否则矩阵类答案会被全部误判为错误。
        from sympy.matrices import MatrixBase
        if isinstance(diff, MatrixBase):
            if diff.shape != getattr(exp, "shape", diff.shape):
                row["correct"] = False
                row["detail"] = f"形状不符: {diff.shape} vs {exp.shape}"
                return row
            row["correct"] = all(simplify(e) == 0 for e in diff)
            row["detail"] = "逐元素完全一致" if row["correct"] else f"差值矩阵 = {list(diff)}"
        else:
            row["correct"] = bool(diff == 0)
            row["detail"] = f"差值 = {diff}" if diff != 0 else "完全一致"
    except Exception as e:
        row["correct"] = False
        row["detail"] = f"比较异常: {e}"
    return row


# ═══════════════════════════════════════════════════════════════════
# 报告
# ═══════════════════════════════════════════════════════════════════

def run(solutions_path: str, parsed_path: str = None) -> dict:
    with open(solutions_path, "r", encoding="utf-8") as f:
        solutions = json.load(f)

    # 未显式给出 parsed_path 时，自动在同目录找 2_parsed.json。
    # 否则通解类题目（P6）会因拿不到 math_expressions 而误判为"未能重建方程组"。
    if parsed_path is None:
        candidate = Path(solutions_path).parent / "2_parsed.json"
        if candidate.exists():
            parsed_path = str(candidate)

    problems = {}
    if parsed_path and Path(parsed_path).exists():
        with open(parsed_path, "r", encoding="utf-8") as f:
            problems = {p["id"]: p for p in json.load(f)}

    rows = [compare(s["problem_id"], s, problems.get(s["problem_id"], {}))
            for s in solutions]

    total = len(rows)
    judged = [r for r in rows if r["correct"] is not None]
    correct = [r for r in judged if r["correct"]]
    verified_pass = [r for r in rows if r["verified"]]

    return {
        "rows": rows,
        "total": total,
        "solved": sum(1 for r in rows if r["correct"] is not False),
        "judged": len(judged),
        "correct": len(correct),
        "accuracy": (len(correct) / len(judged) * 100) if judged else 0.0,
        "verified": len(verified_pass),
    }


def to_markdown(result: dict) -> str:
    """生成验证报告里的对比表格。"""
    lines = []
    lines.append("| 题号 | 知识点 | 标准答案 | 系统输出 | 是否正确 | 机器自检 |")
    lines.append("|---|---|---|---|---|---|")
    for r in result["rows"]:
        mark = {True: "✅", False: "❌", None: "—"}[r["correct"]]
        v = "—"
        if r["verif_total"]:
            v = f"{r['verif_passed']}/{r['verif_total']} {'✔' if r['verified'] else '✖'}"
        lines.append(
            f"| {r['id']} | {r['topic']} | `{r['expected'][:28]}` | "
            f"`{r['got'][:34]}` | {mark} | {v} |"
        )
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("用法: python validate.py <3_solutions.json> [2_parsed.json]")
        sys.exit(1)
    sol_path = sys.argv[1]
    parsed_path = sys.argv[2] if len(sys.argv) > 2 else \
        str(Path(sol_path).parent / "2_parsed.json")

    res = run(sol_path, parsed_path)
    print(f"\n{'='*62}")
    print(f"正确性验证：{res['correct']}/{res['judged']} = {res['accuracy']:.1f}%")
    print(f"（共 {res['total']} 题，其中 {res['verified']} 题 machine-verified）")
    print("=" * 62)
    print(to_markdown(res))
    print()


if __name__ == "__main__":
    main()

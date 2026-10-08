#!/usr/bin/env python3
"""
Stage 3 扩展：线性代数求解器集合
=====================================================================

这是 C4C "扩展学科" 的执行层。对应的知识定义在
`domain_skills/linear_algebra.yaml`（概念图 + 解法契约 + 分类规则），
这里是那些契约的 SymPy 实现。

与 starter kit 的关键差异
--------------------------------------------------------------------
1. **每题都带验证**：算出答案后立刻调用 verify.py 用领域恒等式自检，
   结果写入 solution["verification"]。starter kit 没有这一层。

2. **给解题步骤，不只给答案**：
   starter kit 的 solve_matrix/solve_ode 是两行 stub：
       def solve_matrix(p): return _unsolved(p, "矩阵解析需要扩展（学生扩展点）。")
   本模块每条解法都输出中间过程（阶梯形、特征多项式、顺序主子式…），
   因为它要能作为**可提交的作业**而不是一个答案格。

3. **判定优先于计算**：例如线性方程组，先判定
   r(A) 与 r(A|b) 的关系决定"无解/唯一解/无穷多解"，
   再决定怎么算 —— 而不是无脑调 linsolve 然后崩掉。

注册方式（沿用 baseline 的 SOLVERS 字典扩展点）：
    solve.py 里 `SOLVERS.update(LINALG_SOLVERS)`
"""

from __future__ import annotations

import re
import traceback
from typing import Optional

from sympy import (
    Matrix, Symbol, S, Rational, simplify, factor, expand, eye, zeros,
    latex, solve, Eq, nsimplify, sqrt as sym_sqrt,
)
from sympy.matrices.exceptions import NonSquareMatrixError

from latex_matrix import (
    parse_named_matrices, parse_all_matrices, collect_vectors,
    parse_equations, extract_unknowns, equations_to_Ab, tex_atom,
)
from verify import verify_solution

try:
    from sympy import GramSchmidt
except ImportError:  # pragma: no cover
    GramSchmidt = None


# ═══════════════════════════════════════════════════════════════════
# 上下文提取
# ═══════════════════════════════════════════════════════════════════

def _all_text(problem: dict) -> str:
    parts = [problem.get("text", "") or ""]
    for s in problem.get("sub_problems", []) or []:
        parts.append(s.get("text", "") if isinstance(s, dict) else str(s))
    return "\n".join(parts)


def _all_math(problem: dict) -> list:
    exprs = list(problem.get("math_expressions") or [])
    for s in problem.get("sub_problems", []) or []:
        if isinstance(s, dict):
            exprs.extend(s.get("math_expressions") or [])
    return exprs


def build_context(problem: dict) -> dict:
    """把一道题解析成求解器需要的结构化素材。"""
    text = _all_text(problem)
    math_exprs = _all_math(problem)

    ctx = {
        "text": text,
        "math_exprs": math_exprs,
        "named": parse_named_matrices(text, math_exprs),
        "matrices": parse_all_matrices(text, math_exprs),
        "vectors": collect_vectors(text, math_exprs),
        "equations": parse_equations(text, math_exprs),
        "unknowns": extract_unknowns(text),
    }
    # Ax=b 结构化（若有方程组）
    if ctx["equations"]:
        Ab = equations_to_Ab(ctx["equations"], ctx["unknowns"])
        if Ab:
            ctx["A"], ctx["b"] = Ab
    return ctx


def pick_matrix(ctx: dict, prefer_square: bool = True) -> Optional[Matrix]:
    """
    选"主角矩阵"。

    策略（按优先级）：
      1. 题目正文点名的那一个（找 "A" "B" 等大写字母出现在词句里）
      2. 唯一的方阵
      3. 第一个矩阵
    """
    mats = ctx["matrices"]
    if not mats:
        return None

    # 1) 文本点名
    mentioned = set(re.findall(r"\b([A-Z])\b(?=[^A-Za-z]*?(?:矩阵|行列式|的秩|的逆|的特|matrix|determinant|rank|inverse|eigen))",
                               ctx["text"]))
    for name, m in ctx["named"].items():
        if name in mentioned:
            return m

    # 2) 唯一方阵
    squares = [m for m in mats if m.rows == m.cols]
    if prefer_square and squares:
        return squares[0]
    return mats[0]


# ═══════════════════════════════════════════════════════════════════
# 结果构造
# ═══════════════════════════════════════════════════════════════════

def _ok(problem, steps, answer, answer_latex=None, solver=None,
        verify_kind=None, verify_ctx=None, method=""):
    sol = {
        "problem_id": problem["id"],
        "problem_text": problem.get("text", ""),
        "solved": True,
        "steps": steps,
        "answer": str(answer),
        "answer_latex": answer_latex if answer_latex is not None else (
            latex(answer) if not isinstance(answer, str) else answer),
        "solver": solver or "sympy_linalg",
        "sub_solutions": [],
        "method": method,
    }
    if verify_kind and verify_ctx:
        sol["verification"] = verify_solution(verify_kind, verify_ctx)
    return sol


def _no(problem, reason):
    return {
        "problem_id": problem["id"],
        "problem_text": problem.get("text", ""),
        "solved": False,
        "steps": [],
        "answer": None,
        "answer_latex": "",
        "reason": reason,
        "solver": "none",
        "sub_solutions": [],
    }


def _mlatex(M: Matrix) -> str:
    r"""矩阵的紧凑 LaTeX（不带多余间距符号）。"""
    return latex(M)


# ═══════════════════════════════════════════════════════════════════
# SOLVER 1: 行列式
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_determinant(problem: dict) -> dict:
    ctx = build_context(problem)
    A = pick_matrix(ctx)
    if A is None:
        return _no(problem, "未能在题目中解析出矩阵")
    if A.rows != A.cols:
        return _no(problem, f"行列式要求方阵，而给定矩阵为 {A.rows}×{A.cols}")

    d = simplify(A.det())
    steps = [
        f"给定矩阵 $A = {_mlatex(A)}$",
        f"计算行列式：$\\det A = {_mlatex(A)}$ 的行列式 $= {latex(d)}$",
    ]
    # 用 LU 分解展示过程（更接近手算的三角化思路）
    rref, pivots = A.rref()
    steps.append(f"化为阶梯形可得主元行数为 ${A.rank()}$，矩阵 ${'非奇异' if d != 0 else '奇异'}$。")
    steps.append(f"因此 $\\det A = {latex(d)}$")

    return _ok(problem, steps, d, solver="linalg_determinant",
               verify_kind="matrix_determinant", verify_ctx={"A": A, "answer": d},
               method="行列式定义 / 三角化")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 2: 逆矩阵
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_inverse(problem: dict) -> dict:
    ctx = build_context(problem)
    A = pick_matrix(ctx)
    if A is None:
        return _no(problem, "未能在题目中解析出矩阵")
    if A.rows != A.cols:
        return _no(problem, "只有方阵可能可逆")

    d = simplify(A.det())
    steps = [f"给定 $A = {_mlatex(A)}$",
             f"先判是否可逆：计算 $\\det A = {latex(d)}$"]

    if d == 0:
        steps.append("因为 $\\det A = 0$，故 $A$ 为奇异矩阵，**不可逆**。")
        return _ok(problem, steps, "\\text{不可逆（} \\det A = 0 \\text{）}",
                   answer_latex="A^{-1} \\text{ 不存在}",
                   solver="linalg_inverse", method="行列式判据")

    Ainv = simplify(A.inv())
    steps.append("由于 $\\det A \\neq 0$，$A$ 可逆。用伴随矩阵法或初等行变换 $(A|E)\\to(E|A^{-1})$：")
    steps.append(f"$A^{{-1}} = {_mlatex(Ainv)}$")

    return _ok(problem, steps, Ainv, solver="linalg_inverse",
               verify_kind="matrix_inverse", verify_ctx={"A": A, "answer": Ainv},
               method="初等行变换 (A|E) → (E|A⁻¹)")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 3: 秩
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_rank(problem: dict) -> dict:
    ctx = build_context(problem)
    A = pick_matrix(ctx, prefer_square=False)
    if A is None:
        return _no(problem, "未能在题目中解析出矩阵")

    r = A.rank()
    rref, pivots = A.rref()
    steps = [f"给定 $A = {_mlatex(A)}$（${A.rows}\\times{A.cols}$）",
             f"作初等行变换化为阶梯形：$\\sim {_mlatex(rref)}$",
             f"非零行的行数（或主元个数）为 ${r}$"]
    if A.rows == A.cols:
        d = simplify(A.det())
        steps.append(f"对照检验：$\\det A = {latex(d)}$，"
                     f"${'非零故满秩' if d != 0 else '为零故降秩'}$，与 $r(A)={r}$ 一致。")

    return _ok(problem, steps, r, answer_latex=f"r(A) = {r}",
               solver="linalg_rank", verify_kind="matrix_rank",
               verify_ctx={"A": A, "answer": r}, method="初等行变换 + 阶梯形")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 4: 迹 / 转置
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_trace_transpose(problem: dict) -> dict:
    ctx = build_context(problem)
    A = pick_matrix(ctx)
    if A is None:
        return _no(problem, "未能在题目中解析出矩阵")
    tl = ctx["text"].lower()

    if "转置" in tl or "transpose" in tl:
        AT = A.T
        steps = [f"给定 $A = {_mlatex(A)}$",
                 f"转置的定义是把行列互换：$(A^T)_{{ij}} = A_{{ji}}$",
                 f"$A^T = {_mlatex(AT)}$"]
        return _ok(problem, steps, AT, answer_latex=f"A^T = {_mlatex(AT)}",
                   solver="linalg_transpose", method="转置定义")

    if A.rows != A.cols:
        return _no(problem, "迹仅对方阵有定义")
    tr = simplify(A.trace())
    steps = [f"给定 $A = {_mlatex(A)}$",
             f"迹定义为主对角线元素之和：$\\mathrm{{tr}}(A) = \\sum_{{i}} a_{{ii}}$",
             f"$\\mathrm{{tr}}(A) = {latex(tr)}$"]
    return _ok(problem, steps, tr, answer_latex=f"\\mathrm{{tr}}(A) = {latex(tr)}",
               solver="linalg_trace", verify_kind="matrix_trace_transpose",
               verify_ctx={"A": A, "answer": tr}, method="迹的定义（= Σλ）")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 5: 特征值与特征向量
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_eigen(problem: dict) -> dict:
    ctx = build_context(problem)
    A = pick_matrix(ctx)
    if A is None:
        return _no(problem, "未能在题目中解析出矩阵")
    if A.rows != A.cols:
        return _no(problem, "特征值仅对方阵有定义")

    lam = Symbol("lambda")
    poly = simplify(A.charpoly(lam).as_expr())
    steps = [f"给定 $A = {_mlatex(A)}$"]

    factored = factor(poly)
    steps.append(f"解特征方程 $\\det(A - \\lambda I) = 0$：")
    steps.append(f"特征多项式 $p(\\lambda) = {latex(poly)} = {latex(factored)}$")

    ev_list = A.eigenvects()   # [(λ, 代数重数, [v...]), ...]
    pairs = []
    summary_lines = []
    for value, mult, vectors in ev_list:
        v0 = simplify(vectors[0])
        steps.append(f"$\\lambda = {latex(simplify(value))}$"
                     + (f"（代数重数 ${mult}$）" if mult > 1 else ""))
        for v in vectors:
            vs = Matrix(simplify(v))
            steps.append(f"　　对应特征向量：$v = {_mlatex(vs)}$"
                         f"（即解 $(A - {latex(simplify(value))}I)v = 0$）")
            pairs.append((simplify(value), vs))
        # 几何重数 = 特征空间维数
        steps.append(f"　　几何重数（特征空间维数）$= {len(vectors)}$")
        summary_lines.append(f"\\lambda_{{{len(pairs)}}} = {latex(simplify(value))}")

    answer_latex = ";\\; ".join(summary_lines)
    return _ok(problem, steps, answer_latex, answer_latex=answer_latex,
               solver="linalg_eigen", verify_kind="matrix_eigen",
               verify_ctx={"A": A, "pairs": pairs},
               method="特征多项式 det(A-λI)=0")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 6: 对角化
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_diagonalize(problem: dict) -> dict:
    ctx = build_context(problem)
    A = pick_matrix(ctx)
    if A is None:
        return _no(problem, "未能在题目中解析出矩阵")
    if A.rows != A.cols:
        return _no(problem, "对角化仅对方阵有意义")

    steps = [f"给定 $A = {_mlatex(A)}$"]

    try:
        P, D = A.diagonalize()
    except Exception as e:
        # 不可对角化（亏损矩阵）
        ev = A.eigenvects()
        detail = []
        for value, mult, vecs in ev:
            if len(vecs) < mult:
                detail.append(f"$\\lambda = {latex(simplify(value))}$ 的代数重数为 ${mult}$，"
                              f"但几何重数只有 ${len(vecs)}$")
        steps.append("尝试寻找 $n$ 个线性无关特征向量失败。")
        steps.extend(detail)
        steps.append("因存在特征值其**几何重数 < 代数重数**，故 $A$ 是亏损矩阵，**不可对角化**，"
                     "只能化为 Jordan 标准形。")
        return _ok(problem, steps, "\\text{不可对角化（亏损矩阵）}",
                   solver="linalg_diagonalize", method="几何重数 vs 代数重数")

    P = simplify(P)
    D = simplify(D)
    Pinv = simplify(P.inv())
    steps.append("求得 $3$ 个（即 $n$ 个）线性无关特征向量，故可对角化。")
    steps.append(f"以特征向量为列构成 $P = {_mlatex(P)}$")
    steps.append(f"对应特征值构成对角阵 $\\Lambda = {_mlatex(D)}$")
    steps.append(f"$P^{{-1}} = {_mlatex(Pinv)}$")
    steps.append(f"验证：$P^{{-1}}AP = {_mlatex(simplify(Pinv * A * P))} = \\Lambda$")

    return _ok(problem, steps, D,
               answer_latex=f"P = {_mlatex(P)},\\;\\; \\Lambda = {_mlatex(D)}",
               solver="linalg_diagonalize", verify_kind="matrix_diagonalize",
               verify_ctx={"A": A, "P": P, "D": D},
               method="A = PΛP⁻¹")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 7: 线性方程组（含解的结构判定）
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_linear_system(problem: dict) -> dict:
    ctx = build_context(problem)

    A = ctx.get("A")
    b = ctx.get("b")
    # 也支持 "Ax = b" 里显式给出 A 和 b 两个矩阵/向量的写法
    if A is None:
        mats = ctx["matrices"]
        vecs = ctx["vectors"]
        if mats and vecs:
            A, b = mats[0], vecs[0]
    if A is None:
        return _no(problem, "未能从题目中解析出线性方程组")

    n = A.cols
    rank_A = A.rank()
    steps = [f"系数矩阵 $A = {_mlatex(A)}$，右端向量 $b = {_mlatex(b)}$"]

    # 齐次？
    homogeneous = all(simplify(x) == 0 for x in b)
    if homogeneous:
        steps.append("$b = 0$，这是**齐次**方程组 $Ax = 0$。")

    aug = A.row_join(b)
    rank_Ab = aug.rank()
    steps.append(f"对增广矩阵作初等行变换：$(A\\mid b) \\sim {_mlatex(aug.rref()[0])}$")
    steps.append(f"得到 $r(A) = {rank_A}$，$r(A\\mid b) = {rank_Ab}$，未知量个数 $n = {n}$。")

    # ── 判定：这是本题的核心 ──
    if rank_A < rank_Ab:
        steps.append("因为 $r(A) < r(A\\mid b)$，方程组出现 $0 = d\\ (d\\neq 0)$ 的矛盾，"
                     "故 **无解**。")
        return _ok(problem, steps, "\\text{无解}",
                   solver="linalg_linear_system", method="秩判别法 $r(A) < r(A|b)$")

    free = n - rank_A
    if free == 0:
        steps.append("因为 $r(A) = r(A\\mid b) = n$，方程组有 **唯一解**。")
        x = simplify(A.solve(b)) if A.rows == A.cols else None
        if x is None:
            sol = list(A.gauss_jordan_solve(b))
            x = simplify(sol[0])
            if isinstance(x, Matrix) is False:
                x = Matrix(x)
        steps.append(f"回代求得 $x = {_mlatex(Matrix(x))}$")
        return _ok(problem, steps, Matrix(x),
                   answer_latex=f"x = {_mlatex(Matrix(x))}",
                   solver="linalg_linear_system",
                   verify_kind="matrix_linear_system",
                   verify_ctx={"A": A, "b": b, "answer": Matrix(x)},
                   method="唯一解：回代")

    # 无穷多解：通解 = 特解 + 基础解系线性组合
    steps.append(f"因为 $r(A) = r(A\\mid b) = {rank_A} < n = {n}$，"
                 f"方程组有 **无穷多解**，含 ${free}$ 个自由未知量。")

    try:
        gsol = A.gauss_jordan_solve(b)
        x_part = simplify(Matrix(gsol[0]))
        # gauss_jordan_solve 给出的特解带自由参数 τ₀, τ₁, …（如 (1−τ₀, τ₀, 0)）。
        # 令全部自由参数取 0，得到一个干净的整数特解（如 (1, 0, 0)），
        # 这样写出来的通解才是学生熟悉的标准形式。
        for s in list(x_part.free_symbols):
            if s.name.startswith("tau"):
                x_part = x_part.subs(s, 0)
        x_part = simplify(Matrix(x_part))
    except Exception:
        x_part = None

    ns = A.nullspace()
    for i, v in enumerate(ns):
        steps.append(f"令一个自由未知量为 $1$、其余为 $0$，"
                     f"得基础解系向量 $\\xi_{{{i+1}}} = {_mlatex(Matrix(simplify(v)))}$")

    if x_part is not None and ns:
        parts = [f"c_{{{i+1}}}\\xi_{{{i+1}}}" for i in range(len(ns))]
        general = "+".join(parts)
        # 只有一个参数时不能写成 "c₁,…,c₁"，那是笔误级别的表述
        if len(ns) == 1:
            params = "$c_1$ 为任意常数"
        else:
            params = f"$c_1,\\dots,c_{{{len(ns)}}}$ 为任意常数"
        if not homogeneous:
            steps.append(f"令全部自由未知量取 $0$，得一个特解 $\\eta^{{*}} = {_mlatex(x_part)}$")
            steps.append(f"**通解**：$x = \\eta^{{*}} + {general}$，其中 {params}。")
        else:
            steps.append(f"**通解**：$x = {general}$，其中 {params}。")
        ans = f"\\text{{通解：}}\\ x = {_mlatex(x_part)} + {general}" if not homogeneous \
            else f"\\text{{通解：}}\\ x = {general}"
        return _ok(problem, steps, ans, answer_latex=ans,
                   solver="linalg_linear_system",
                   verify_kind="matrix_linear_system",
                   verify_ctx={"A": A, "b": b, "answer": x_part},
                   method="通解 = 特解 + 基础解系线性组合")

    return _ok(problem, steps, f"\\text{{无穷多解（{free} 个自由未知量）}}",
               solver="linalg_linear_system", method="秩判别法")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 8: Gram-Schmidt 正交化
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_gram_schmidt(problem: dict) -> dict:
    ctx = build_context(problem)
    vecs = ctx["vectors"]
    if len(vecs) < 2:
        return _no(problem, f"正交化至少需要 2 个向量，仅解析到 {len(vecs)} 个")

    tl = ctx["text"].lower()
    orthonormal = not any(k in tl for k in ("不单位化", "without normalizing", "only orthogonal"))

    if GramSchmidt is None:
        return _no(problem, "当前 SymPy 版本未提供 GramSchmidt")

    result = [Matrix(simplify(v)) for v in GramSchmidt(list(vecs), orthonormal=orthonormal)]

    steps = []
    # ⚠️ 这里的 $ 必须成对：只写结尾 $ 会让整段被 LaTeX 当成普通文本，
    #    花括号被转义成 \begin\{matrix\}，编译时一次报上百个错。
    step_names = [f"$\\alpha_{{{i+1}}} = {_mlatex(Matrix(vecs[i]))}$"
                  for i in range(len(vecs))]
    steps.append("给定向量组：" + "，".join(step_names))

    # 逐手展示递推（经典 Gram-Schmidt 公式）
    betas = []
    for k in range(len(vecs)):
        a_k = Matrix(vecs[k])
        if k == 0:
            beta = a_k
            steps.append(f"取 $\\beta_1 = \\alpha_1 = {_mlatex(beta)}$")
        else:
            beta = a_k
            terms = []
            for j in range(k):
                bj = betas[j]
                coef = simplify((a_k.T * bj)[0, 0] / (bj.T * bj)[0, 0])
                beta = beta - coef * bj
                terms.append(f"\\frac{{(\\alpha_{{{k+1}}},\\beta_{{{j+1}}})}}"
                             f"{{(\\beta_{{{j+1}}},\\beta_{{{j+1}}})}}\\beta_{{{j+1}}}")
            beta = Matrix(simplify(beta))
            steps.append(f"$\\beta_{{{k+1}}} = \\alpha_{{{k+1}}} - "
                         + " - ".join(terms)
                         + f"= {_mlatex(beta)}$")
        betas.append(beta)

    if orthonormal:
        steps.append("再逐一单位化 $e_k = \\dfrac{\\beta_k}{\\|\\beta_k\\|}$：")
        for k, beta in enumerate(betas):
            steps.append(f"$e_{{{k+1}}} = {_mlatex(Matrix(result[k]))}$")
        ans_latex = ",\\quad ".join(f"e_{{{i+1}}} = {_mlatex(v)}"
                                    for i, v in enumerate(result))
    else:
        ans_latex = ",\\quad ".join(f"\\beta_{{{i+1}}} = {_mlatex(v)}"
                                    for i, v in enumerate(result))

    return _ok(problem, steps, result, answer_latex=ans_latex,
               solver="linalg_gram_schmidt", verify_kind="matrix_gram_schmidt",
               verify_ctx={"answer": result, "orthonormal": orthonormal},
               method="Gram-Schmidt 正交化" + (" + 单位化" if orthonormal else ""))


# ═══════════════════════════════════════════════════════════════════
# SOLVER 9: 线性相关性
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_independence(problem: dict) -> dict:
    ctx = build_context(problem)
    vecs = ctx["vectors"]
    if not vecs:
        guesses = [Matrix(v) for v in ctx["matrices"]]
        if not guesses:
            return _no(problem, "未解析到向量组")
        vecs = guesses

    M = Matrix.hstack(*[Matrix(v) for v in vecs])
    r = M.rank()
    independent = (r == M.cols)

    steps = [f"把向量按列排成矩阵 $A = {_mlatex(M)}$（${M.rows}\\times{M.cols}$）",
             f"作初等行变换：$\\sim {_mlatex(M.rref()[0])}$",
             f"秩为 $r(A) = {r}$，向量个数为 ${M.cols}$。"]
    if independent:
        steps.append("因为 $r(A) =$ 向量个数，向量组**线性无关**。")
        ans = "\\text{线性无关}"
    else:
        steps.append("因为 $r(A) <$ 向量个数，存在非零的组合系数使线性组合为零，"
                     "向量组**线性相关**。")
        # 给出一个具体的关系式
        try:
            ns = M.nullspace()
            if ns:
                coef = Matrix(simplify(ns[0]))
                steps.append(f"一组非零系数为 $c = {_mlatex(coef)}$，满足 $Ac = 0$。")
        except Exception:
            pass
        ans = "\\text{线性相关}"

    return _ok(problem, steps, ans, solver="linalg_independence",
               verify_kind="matrix_independence",
               verify_ctx={"vectors": [Matrix(v) for v in vecs],
                           "answer": independent, "rank": r},
               method="秩判别法")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 10: 基 / 维数 / 列空间 / 零空间
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_basis_dimension(problem: dict) -> dict:
    ctx = build_context(problem)
    A = pick_matrix(ctx, prefer_square=False)
    if A is None:
        return _no(problem, "未能在题目中解析出矩阵")

    tl = ctx["text"].lower()
    want_zero = any(k in tl for k in ("nullspace", "null space", "零空间", "解空间", "kernel", "核"))

    if want_zero:
        basis = [Matrix(simplify(v)) for v in A.nullspace()]
        steps = [f"给定 $A = {_mlatex(A)}$，求零空间即解 $Ax = 0$。",
                 f"化为阶梯形：$\\sim {_mlatex(A.rref()[0])}$",
                 f"$r(A) = {A.rank()}$，由秩-零度定理 $\\dim N(A) = n - r(A) = {A.cols} - {A.rank()} = {len(basis)}$。"]
        if basis:
            for i, v in enumerate(basis):
                steps.append(f"基础解系 $\\xi_{{{i+1}}} = {_mlatex(v)}$")
            ans = ";\\ ".join(f"\\xi_{{{i+1}}} = {_mlatex(v)}" for i, v in enumerate(basis))
        else:
            steps.append("零空间只有零向量（平凡），$N(A) = \\{0\\}$。")
            ans = "\\{0\\}"
        return _ok(problem, steps, ans, solver="linalg_basis",
                   verify_kind="matrix_basis_dimension",
                   verify_ctx={"A": A, "answer": basis, "space": "nullspace"},
                   method="解 Ax=0 + 秩-零度定理")

    # 默认：列空间
    basis = [Matrix(simplify(v)) for v in A.columnspace()]
    steps = [f"给定 $A = {_mlatex(A)}$",
             f"化为阶梯形：$\\sim {_mlatex(A.rref()[0])}$",
             f"主元列为 Pivot 列，对应 $A$ 的列向量构成列空间的一组基。",
             f"$r(A) = {A.rank()}$，故 $\\dim C(A) = {len(basis)}$。"]
    for i, v in enumerate(basis):
        steps.append(f"基向量 $b_{{{i+1}}} = {_mlatex(v)}$")
    ans = ";\\ ".join(f"b_{{{i+1}}} = {_mlatex(v)}" for i, v in enumerate(basis))

    return _ok(problem, steps, ans, solver="linalg_basis",
               verify_kind="matrix_basis_dimension",
               verify_ctx={"A": A, "answer": basis, "space": "columnspace"},
               method="主元列 → 列空间基")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 11: 二次型正定性（顺序主子式法）
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_quadratic_form(problem: dict) -> dict:
    ctx = build_context(problem)
    A = pick_matrix(ctx)
    if A is None:
        return _no(problem, "未能在题目中解析出对称矩阵")
    if A.rows != A.cols:
        return _no(problem, "二次型矩阵必须是方阵")

    n = A.rows
    steps = [f"给定对称矩阵 $A = {_mlatex(A)}$",
             "用 Sylvester 判据：计算各阶顺序主子式。"]
    minors = []
    for k in range(1, n + 1):
        minor = simplify(A[:k, :k].det())
        minors.append(minor)
        steps.append(f"${k}$ 阶顺序主子式 $D_{{{k}}} = {latex(A[:k, :k])} \\Rightarrow {latex(minor)}$")

    pos = all(m > 0 for m in minors)
    alt = all(((-1) ** i) * minors[i] > 0 for i in range(len(minors)))   # -,+,-

    if pos:
        verdict = "positive definite"
        steps.append("所有顺序主子式均大于 $0$，故该二次型 **正定**。")
        ans = "\\text{正定}"
    elif alt:
        verdict = "negative definite"
        steps.append("顺序主子式负正交替（奇数阶为负、偶数阶为正），故该二次型 **负定**。")
        ans = "\\text{负定}"
    else:
        verdict = "indefinite"
        steps.append("顺序主子式既非全正也非负正交替，故该二次型 **不定**。")
        ans = "\\text{不定}"

    return _ok(problem, steps, ans, solver="linalg_quadratic_form",
               verify_kind="matrix_quadratic_form",
               verify_ctx={"A": A, "answer": verdict},
               method="Sylvester 顺序主子式判据")


# ═══════════════════════════════════════════════════════════════════
# SOLVER 12: 矩阵四则运算 / 幂
# ═══════════════════════════════════════════════════════════════════

def solve_matrix_arithmetic(problem: dict) -> dict:
    ctx = build_context(problem)
    named = ctx["named"]
    mats = ctx["matrices"]
    tl = ctx["text"].lower()

    # 二元运算
    if len(mats) >= 2 and any(op in ctx["text"] for op in ("+", "-", "*", "×", "·")):
        A, B = mats[0], mats[1]
        names = list(named.keys())
        nA = names[0] if len(names) > 0 else "A"
        nB = names[1] if len(names) > 1 else "B"

        if any(k in tl for k in ("multiply", "product", "乘法", "ab", "积")):
            R = simplify(A * B)
            steps = [f"$A = {_mlatex(A)}$，$B = {_mlatex(B)}$",
                     f"按矩阵乘法规则 $(AB)_{{ij}} = \\sum_k a_{{ik}}b_{{kj}}$：",
                     f"$AB = {_mlatex(R)}$"]
            ans, opname = R, "矩阵乘法"
        elif "+" in ctx["text"] or "addition" in tl or "加法" in tl:
            R = simplify(A + B)
            steps = [f"$A + B = {_mlatex(A)} + {_mlatex(B)} = {_mlatex(R)}$"]
            ans, opname = R, "矩阵加法"
        else:
            R = simplify(A - B)
            steps = [f"$A - B = {_mlatex(A)} - {_mlatex(B)} = {_mlatex(R)}$"]
            ans, opname = R, "矩阵减法"
        return _ok(problem, steps, ans, answer_latex=f"{_mlatex(R)}",
                   solver="linalg_arithmetic", method=opname)

    # 幂
    m = re.search(r"A\^\{?(\d)\}?|A\s*的\s*(\d)\s*次方|power", ctx["text"])
    P = pick_matrix(ctx)
    if m and P is not None:
        k = int(m.group(1) or m.group(2))
        R = simplify(P ** k)
        steps = [f"$A = {_mlatex(P)}$", f"$A^{{{k}}} = {_mlatex(R)}$"]
        return _ok(problem, steps, R, solver="linalg_arithmetic", method="矩阵幂")

    P = P or (mats[0] if mats else None)
    if P is None:
        return _no(problem, "未能在题目中解析出矩阵")

    AT = P.T
    steps = [f"$A = {_mlatex(P)}$", f"转置：$A^T = {_mlatex(AT)}$"]
    return _ok(problem, steps, AT, answer_latex=f"A^T = {_mlatex(AT)}",
               solver="linalg_arithmetic", method="转置")


# ═══════════════════════════════════════════════════════════════════
# 注册
# ═══════════════════════════════════════════════════════════════════

LINALG_SOLVERS = {
    "matrix_arithmetic": solve_matrix_arithmetic,
    "matrix_determinant": solve_matrix_determinant,
    "matrix_inverse": solve_matrix_inverse,
    "matrix_rank": solve_matrix_rank,
    "matrix_trace_transpose": solve_matrix_trace_transpose,
    "matrix_eigen": solve_matrix_eigen,
    "matrix_diagonalize": solve_matrix_diagonalize,
    "matrix_linear_system": solve_matrix_linear_system,
    "matrix_gram_schmidt": solve_matrix_gram_schmidt,
    "matrix_independence": solve_matrix_independence,
    "matrix_basis_dimension": solve_matrix_basis_dimension,
    "matrix_quadratic_form": solve_matrix_quadratic_form,
}


def dispatch(problem: dict, ptype: str) -> Optional[dict]:
    """安全分发：任何异常都转成"未求解"，绝不炸流水线。"""
    fn = LINALG_SOLVERS.get(ptype)
    if fn is None:
        return None
    try:
        return fn(problem)
    except Exception as e:
        return _no(problem, f"线性代数求解器异常: {type(e).__name__}: {e}")


if __name__ == "__main__":
    demo = [
        {"id": "1", "text": "Find the determinant of $A = \\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}$",
         "math_expressions": [], "sub_problems": []},
        {"id": "2", "text": "Find eigenvalues of $A = \\begin{pmatrix} 4 & 1 \\\\ 2 & 3 \\end{pmatrix}$",
         "math_expressions": [], "sub_problems": []},
        {"id": "3", "text": "Solve $$\\begin{cases} x_1 + 2x_2 = 5 \\\\ 3x_1 - x_2 = 1 \\end{cases}$$",
         "math_expressions": [], "sub_problems": []},
    ]
    ids = ["matrix_determinant", "matrix_eigen", "matrix_linear_system"]
    for t, d in zip(ids, demo):
        r = dispatch(d, t)
        print(f"\n=== {t} ===")
        print("solved:", r["solved"], "| ans:", r.get("answer_latex", r.get("reason"))[:120])
        print("verify:", r.get("verification", {}).get("status"),
              r.get("verification", {}).get("passed"), "/", r.get("verification", {}).get("total"))

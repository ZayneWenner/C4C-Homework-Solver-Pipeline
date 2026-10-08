#!/usr/bin/env python3
"""
Stage 3.5: 答案验证器（Answer Verifier）
=====================================================================

为什么需要独立的一层？
--------------------------------------------------------------------
Starter kit 的 solver 是"输出即答案"：算出什么就往 .tex 里写什么，
没有任何自检。这导致一个危险的失败模式 —— **错误被排版得很漂亮**。

对教学场景这是不能接受的。所以本工程在 Stage 3（求解）与 Stage 4（渲染）
之间插入了一个小而关键的 Stage：
任何 solver 给出的答案，必须先用领域恒等式验证，再进入渲染。

每条验证律都直接对应 linear_algebra.yaml 里的 validation_rules，
T-box 里写"数学上必然成立的关系"，这里写"如何用 SymPy 检查它"。
知识定义与检查实现分离，和 starter kit 的整体哲学保持一致。

设计原则
--------------------------------------------------------------------
1. **验证失败不静默**：验证结果写进 solution["verification"]，
   渲染时显示在 PDF 里。一道 verified=false 的题比一道没验证的题
   对用户更有价值 —— 它明确告诉用户"这个别直接抄"。

2. **验证通过才敢说正确**：正确性报告里的"正确率"必须区分
   "已解题数" 与"已验证解题数"。后者才是真正可提交的那部分。

3. **容差处理浮点**：所有数值检查用 sympy 精确比较优先，
   退化到 float 时用 1e-9 容差，避免 √2 之类无理数误判。

Usage:
    from verify import verify_solution
    sol["verification"] = verify_solution(kind, ctx, answer_obj)
"""

from __future__ import annotations

from typing import Any

from sympy import (
    simplify, eye, zeros, Matrix, S, nsimplify, Abs, Rational,
    latex as sym_latex,
)

TOL = S(10) ** -9


def _zero_matrix(M: Matrix) -> bool:
    """M 是否为零矩阵（先 symbolic simplify，再用数值兜底）。"""
    try:
        return simplify(M) == zeros(*M.shape)
    except Exception:
        try:
            return all(abs(float(x)) < 1e-9 for x in M)
        except Exception:
            return False


def _close(a, b) -> bool:
    """象征/数值双路相等判断。"""
    try:
        if simplify(a - b) == 0:
            return True
    except Exception:
        pass
    try:
        fa, fb = complex(a.evalf()), complex(b.evalf())
        return abs(fa - fb) < 1e-9
    except Exception:
        return False


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "passed": bool(ok), "detail": detail}


# ═══════════════════════════════════════════════════════════════════
# 各领域的验证律
# ═══════════════════════════════════════════════════════════════════

def verify_inverse(A: Matrix, Ainv: Matrix) -> list[dict]:
    """验证规则 inverse_sanity: A · A⁻¹ = I"""
    checks = []
    try:
        prod = simplify(A * Ainv)
        ok = prod == eye(A.rows)
        detail = f"A·A⁻¹ = {sym_latex(prod)}" if not ok else ""
        checks.append(_check("inverse_sanity", ok, detail))
    except Exception as e:
        checks.append(_check("inverse_sanity", False, f"检查异常: {e}"))

    # 附带检查：可逆 ⟺ det ≠ 0
    try:
        det_zero = simplify(A.det()) == 0
        checks.append(_check("invertible_det", not det_zero,
                             f"det(A) = {sym_latex(A.det())}"))
    except Exception:
        pass
    return checks


def verify_determinant(A: Matrix, det_val) -> list[dict]:
    """det(A) = Π λ_i（就算 "det_multiplicative"）"""
    checks = []
    try:
        evs = A.eigenvals()
        prod = S(1)
        for lam, mult in evs.items():
            prod *= lam ** mult
        ok = _close(prod, det_val)
        checks.append(_check("det_multiplicative", ok,
                             f"Πλ = {sym_latex(simplify(prod))}, det = {sym_latex(det_val)}"))
    except Exception as e:
        checks.append(_check("det_multiplicative", False, f"无法计算特征值乘积: {e}"))
    return checks


def verify_trace(A: Matrix, tr_val) -> list[dict]:
    """tr(A) = Σ λ_i"""
    try:
        evs = A.eigenvals()
        s = sum((lam * mult for lam, mult in evs.items()), S(0))
        return [_check("trace_additive", _close(s, tr_val),
                       f"Σλ = {sym_latex(simplify(s))}, tr = {sym_latex(tr_val)}")]
    except Exception as e:
        return [_check("trace_additive", False, f"异常: {e}")]


def verify_eigenpairs(A: Matrix, pairs: list[tuple]) -> list[dict]:
    """对每个 (λ, v) 检查 A v = λ v"""
    checks = []
    for i, (lam, vec) in enumerate(pairs):
        try:
            v = vec if isinstance(vec, Matrix) else Matrix(vec)
            residual = simplify(A * v - lam * v)
            ok = _zero_matrix(residual)
            checks.append(_check(f"eigen_residual[{i}]", ok,
                                 f"λ={sym_latex(lam)}, 残差={sym_latex(residual)}"[:200]))
        except Exception as e:
            checks.append(_check(f"eigen_residual[{i}]", False, f"异常: {e}"))
    return checks


def verify_diagonalization(A: Matrix, P: Matrix, D: Matrix) -> list[dict]:
    """A = P D P⁻¹"""
    checks = []
    try:
        Pinv = P.inv()
        recon = simplify(P * D * Pinv)
        ok = recon == A
        checks.append(_check("diagonalization_check", ok,
                             f"PDP⁻¹ = {sym_latex(recon)}"[:200]))
    except Exception as e:
        checks.append(_check("diagonalization_check", False, f"异常: {e}"))

    # P 必须可逆
    try:
        checks.append(_check("P_invertible", simplify(P.det()) != 0,
                             f"det(P) = {sym_latex(simplify(P.det()))}"))
    except Exception:
        pass
    return checks


def verify_orthonormality(vectors: list[Matrix], orthonormal: bool = True) -> list[dict]:
    """(e_i, e_j) = δ_ij；若 orthonormal 还要 ||e_i|| = 1"""
    checks = []
    n = len(vectors)
    worst_pair = None
    ok = True
    for i in range(n):
        for j in range(i + 1, n):
            try:
                d = simplify((vectors[i].T * vectors[j])[0, 0])
                if d != 0:
                    ok = False
                    worst_pair = f"(e{i+1}, e{j+1}) = {sym_latex(d)}"
            except Exception:
                ok = False
    checks.append(_check("orthogonality_check", ok, worst_pair or ""))

    if orthonormal:
        norms_ok, detail = True, ""
        for i, v in enumerate(vectors):
            try:
                nn = simplify((v.T * v)[0, 0])
                if nn != 1:
                    norms_ok = False
                    detail = f"||e{i+1}||² = {sym_latex(nn)}"
            except Exception:
                norms_ok = False
        checks.append(_check("unit_norm_check", norms_ok, detail))
    return checks


def verify_rank_consistency(A: Matrix, rank_val: int) -> list[dict]:
    """det(A) ≠ 0 ⟺ rank(A) = n（仅方阵）"""
    checks = []
    if A.rows != A.cols:
        return checks
    try:
        det_zero = simplify(A.det()) == 0
        full = (rank_val == A.rows)
        ok = (det_zero != full)
        checks.append(_check("rank_determinant_consistency", ok,
                             f"det(A){'=' if det_zero else '≠'}0, rank={rank_val}/{A.rows}"))
    except Exception:
        pass
    return checks


def verify_solution_of_system(A: Matrix, b: Matrix, x: Matrix | None,
                              particular_ok: bool = True) -> list[dict]:
    """代入验证 A x = b"""
    checks = []
    if x is None:
        return [_check("system_residual", False, "无解向量可代入")]
    try:
        residual = simplify(A * x - b)
        ok = _zero_matrix(residual)
        checks.append(_check("system_residual", ok,
                             f"Ax - b = {sym_latex(residual)}"[:200]))
    except Exception as e:
        checks.append(_check("system_residual", False, f"异常: {e}"))
    return checks


def verify_nullspace(A: Matrix, basis: list[Matrix]) -> list[dict]:
    """dim N(A) = n - rank(A)"""
    checks = []
    try:
        ok = len(basis) == A.cols - A.rank()
        checks.append(_check("nullspace_consistency", ok,
                             f"dim N(A) = {len(basis)}, n - rank = {A.cols - A.rank()}"))
    except Exception:
        pass
    # 基向量确实被 A 映到 0
    for i, v in enumerate(basis):
        try:
            checks.append(_check(f"nullspace_map[{i}]", _zero_matrix(simplify(A * v)), ""))
        except Exception:
            pass
    return checks


def verify_independence(vectors: list[Matrix], claimed_independent: bool,
                        claimed_rank: int) -> list[dict]:
    """rank([v1…vn]) 与"是否无关"的结论必须一致"""
    try:
        M = Matrix.hstack(*[v if isinstance(v, Matrix) else Matrix(v)
                            for v in vectors])
        r = M.rank()
        actual_indep = (r == M.cols)
        ok = (actual_indep == claimed_independent) and (r == claimed_rank)
        return [_check("independence_consistency", ok,
                       f"实际 rank = {r}/{M.cols}, 判定为"
                       f"{'无关' if claimed_independent else '相关'}")]
    except Exception as e:
        return [_check("independence_consistency", False, f"异常: {e}")]


def verify_quadratic_form(A: Matrix, verdict: str) -> list[dict]:
    """用特征值重算一次定性，与顺序主子式的结论对照"""
    try:
        evs = [v.evalf() for v in A.eigenvals().keys()]
        reals = []
        for v in evs:
            try:
                re = complex(v).real
                reals.append(re)
            except Exception:
                pass
        if not reals:
            return []
        if all(r > 1e-9 for r in reals):
            actual = "positive definite"
        elif all(r < -1e-9 for r in reals):
            actual = "negative definite"
        elif all(r >= -1e-9 for r in reals):
            actual = "positive semidefinite"
        elif all(r <= 1e-9 for r in reals):
            actual = "negative semidefinite"
        else:
            actual = "indefinite"
        ok = actual.replace("_", " ") in verdict.lower() or verdict.lower() in actual
        return [_check("quadratic_form_consistency", ok,
                       f"顺序主子式判据='{verdict}', 特征值判据='{actual}'")]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════════

def aggregate(checks: list[dict]) -> dict:
    """把若干条 check 汇总成一个 verification 块。"""
    if not checks:
        return {"verified": False, "status": "unchecked",
                "passed": 0, "total": 0, "checks": [],
                "note": "此题型暂无可执行验证律"}
    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    return {
        "verified": passed == total,
        "status": "verified" if passed == total else (
            "partially_verified" if passed else "failed"),
        "passed": passed,
        "total": total,
        "checks": checks,
    }


def verify_solution(kind: str, ctx: dict) -> dict:
    """
    按题型分发验证。ctx 由 solver 提供所需的矩阵与答案对象。

    kind 对应 linear_algebra.yaml 里 solution_methods 的 id。
    """
    handlers = {
        "matrix_inverse": lambda: verify_inverse(ctx["A"], ctx["answer"]),
        "matrix_determinant": lambda: verify_determinant(ctx["A"], ctx["answer"]),
        "matrix_trace_transpose": lambda: verify_trace(ctx["A"], ctx["answer"]),
        "matrix_eigen": lambda: verify_eigenpairs(ctx["A"], ctx["pairs"]),
        "matrix_diagonalize": lambda: verify_diagonalization(
            ctx["A"], ctx["P"], ctx["D"]),
        "matrix_gram_schmidt": lambda: verify_orthonormality(
            ctx["answer"], ctx.get("orthonormal", True)),
        "matrix_rank": lambda: verify_rank_consistency(ctx["A"], ctx["answer"]),
        "matrix_linear_system": lambda: verify_solution_of_system(
            ctx["A"], ctx["b"], ctx.get("answer")),
        "matrix_basis_dimension": lambda: verify_nullspace(
            ctx["A"], ctx.get("answer") or []) if ctx.get("space") == "nullspace" else [],
        "matrix_independence": lambda: verify_independence(
            ctx["vectors"], ctx["answer"], ctx.get("rank", -1)),
        "matrix_quadratic_form": lambda: verify_quadratic_form(
            ctx["A"], ctx["answer"]),
    }
    fn = handlers.get(kind)
    if fn is None:
        return aggregate([])
    try:
        return aggregate(fn())
    except Exception as e:
        return {"verified": False, "status": "error", "passed": 0, "total": 0,
                "checks": [_check(kind, False, f"验证过程异常: {e}")],
                "note": str(e)}


def format_verification_latex(v: dict) -> str:
    """把 verification 结果渲染成 LaTeX 里的一行备注（用于 PDF）。"""
    if not v:
        return ""
    icon = {"verified": r"\checkmark", "partially_verified": r"\sim",
            "failed": r"\times", "unchecked": "?", "error": r"\times"}.get(v["status"], "?")
    core = f"$ {icon} \\; \\text{{验证}}: {v['passed']}/{v['total']} \\text{{ 条恒等式通过}}$"
    failed = [c for c in v.get("checks", []) if not c["passed"]]
    if failed:
        detail = "; ".join(c["detail"][:90] for c in failed if c.get("detail"))
        if detail:
            core += r" \\ \small\textit{" + detail.replace("%", r"\%") + r"}"
    return core

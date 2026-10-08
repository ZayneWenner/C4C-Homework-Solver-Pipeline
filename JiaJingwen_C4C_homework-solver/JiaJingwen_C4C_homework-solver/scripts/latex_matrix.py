#!/usr/bin/env python3
r"""
线性代数专用 LaTeX → SymPy 解析层
=====================================================================

为什么不用 starter kit 自带的 safe_parse？
--------------------------------------------------------------------
看 baseline 的 solve.py:

    s = re.sub(r"_\{[^}]+\}", "", s)     # ← 把 x_{1} 直接删掉
    s = re.sub(r"_\\w", "", s)           # ← 把 x_1 直接删掉

这两行对微积分无害（那里下标基本不出现），但对线性代数是**致命**的：
    x_1 + 2x_2 - x_3 = 0   →   x + 2x - x = 0   （三个未知量塌缩成一个）

所以线性代数域需要自己的解析器，必须正确处理：
  · 下标作为符号名的一部分:  x_1 → Symbol('x1'),  x_{12} → Symbol('x12')
  · 矩阵环境:  pmatrix / bmatrix / vmatrix / array
  · 方程组环境: cases
  · 矩阵转置记号: A^{T} / A^T / A'

解析策略：先在 LaTeX 层面做结构切分（组/行/列/方程），
每个原子片段再交给 sympy 解析。这样既能保住结构，又不用手写完整 TeX 解析器。
"""

from __future__ import annotations

import re

from sympy import (
    Matrix, Symbol, Integer, Rational, Float, sympify, Eq, sqrt, Abs,
)
from sympy.parsing.sympy_parser import (
    parse_expr, standard_transformations,
    implicit_multiplication_application, convert_xor, auto_symbol,
)

TRANSFORMS = standard_transformations + (
    implicit_multiplication_application, convert_xor, auto_symbol,
)

# 预生成 x1..x9 / y1..y9 / z1..z9 / a1..a9 之类的符号，供方程组使用
def _sym_table():
    table = {}
    for base in "xyzabcuvw":
        table[base] = Symbol(base)
        for i in range(1, 13):
            table[f"{base}{i}"] = Symbol(f"{base}{i}")
    # 常见参数符号
    for name in ("lamda", "t", "k", "n", "m", "r", "s"):
        table[name] = Symbol(name)
    # λ (特征值) / ε / δ
    table["lambda"] = Symbol("lambda")
    table["varepsilon"] = Symbol("varepsilon")
    table["delta"] = Symbol("delta")
    return table

SYMS = _sym_table()

_MATRIX_ENV_RE = re.compile(
    r"\\begin\{(p|b|v|V|B)?matrix\*?\}(.*?)\\end\{\1?matrix\*?\}", re.DOTALL
)
_CASES_RE = re.compile(r"\\begin\{cases\*?\}(.*?)\\end\{cases\*?\}", re.DOTALL)
_ARRAY_RE = re.compile(r"\\begin\{array\}\{[^}]*\}(.*?)\\end\{array\}", re.DOTALL)


# ═══════════════════════════════════════════════════════════════════
# 原子片段级别的 LaTeX → SymPy
# ═══════════════════════════════════════════════════════════════════

def _norm_subscripts(s: str) -> str:
    r"""
    把下标改写成合法 Python 标识符的一部分。

    x_{1} → x1      x_1 → x1      a_{ij} → aij
    注意：这一步必须发生在删注释/删命令之前，否则下标会被别的规则吃掉。
    """
    # x_{123} → x123
    s = re.sub(r"([A-Za-z])_\{([^}]+)\}", lambda m: m.group(1) + _flatten(m.group(2)), s)
    # x_1 → x1  (只吃紧邻的一个字符或一个数字串)
    s = re.sub(r"([A-Za-z])_([A-Za-z0-9]+)", lambda m: m.group(1) + m.group(2), s)
    return s


def _flatten(body: str) -> str:
    """把 a_{ij} 里的 ij 变成 ij（去空格）；嵌套 _ 时递归一层即可。"""
    body = re.sub(r"\s+", "", body)
    body = re.sub(r"_\{([^}]+)\}", lambda m: m.group(1), body)
    return re.sub(r"[^A-Za-z0-9]", "", body)


def tex_atom(s: str):
    """
    把一个**不含矩阵结构**的 LaTeX 片段解析成 SymPy 表达式。

    只处理线性代数作业里真实会出现的记号，其余按尽力而为原则退化处理。
    """
    if s is None:
        raise ValueError("空片段")
    t = s.strip()

    t = _norm_subscripts(t)

    # 环境残留清理
    t = re.sub(r"\\begin\{.*?\}|\\end\{.*?\}", "", t)

    # \frac —— 支持嵌套括号
    while r"\frac{" in t or "\\frac" in t:
        m = re.search(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", t)
        if not m:
            t = re.sub(r"\\frac\s*(\d)\s*(\d)", r"((\1)/(\2))", t)
            if "\\frac" in t:
                t = t.replace(r"\frac", " ")
            break
        t = t[:m.start()] + f"(({m.group(1)})/({m.group(2)}))" + t[m.end():]

    # \dfrac / \tfrac
    t = re.sub(r"\\[dt]frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", t)

    # 根式
    t = re.sub(r"\\sqrt\[(\d+)\]\{([^{}]*)\}", r"((\2)**(1/(\1)))", t)
    t = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", t)

    # 二项式/三角（线性代数用得少，但保留容错）
    t = re.sub(r"\\binom\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", t)

    # 常见符号与运算符
    replacements = {
        r"\cdot": "*", r"\times": "*", r"\div": "/", r"\pm": "+",
        r"\mp": "-", r"\circ": "*",
        r"\pi": "pi", r"\infty": "oo", r"\lambda": "lambda",
        r"\varepsilon": "varepsilon", r"\epsilon": "varepsilon",
        r"\delta": "delta", r"\theta": "theta", r"\alpha": "alpha",
        r"\beta": "beta", r"\mu": "mu", r"\sigma": "sigma",
        r"\left": "", r"\right": "",
        r"\,": " ", r"\;": " ", r"\!": "", r"\:": " ",
        r"\quad": " ", r"\qquad": " ", r"\\": " ",
        r"\{": "(", r"\}": ")",
    }
    for a, b in replacements.items():
        t = t.replace(a, b)

    # 常见的文字包裹
    t = re.sub(r"\\(?:mathrm|mathbf|boldsymbol|mathit|text|textbf|operatorname)\s*\{([^{}]*)\}", r"\1", t)

    # 矩阵/向量装饰 :: \vec{x} → x
    t = re.sub(r"\\(?:vec|hat|bar|tilde|dot)\s*\{([^{}]*)\}", r"\1", t)

    # 绝对值（作为 Abs）
    t = re.sub(r"\|([^|]*)\|", r"Abs(\1)", t)

    # 转置记号在原子层面直接忽略（T' 不是合法符号）
    t = re.sub(r"\^\s*\{?T\}?", "", t)
    t = re.sub(r"\^\s*\\top", "", t)

    # 幂
    t = re.sub(r"\^\{([^{}]*)\}", r"**(\1)", t)
    t = re.sub(r"\^\s*([A-Za-z0-9])", r"**\1", t)

    # 未知命令一律删掉（宁可少一截，也不要 parse 失败）
    t = re.sub(r"\\[a-zA-Z]+", " ", t)

    # 剩余的分组括号转圆括号
    t = t.replace("{", "(").replace(")", ")")
    t = re.sub(r"\s+", " ", t).strip()

    if not t:
        raise ValueError(f"解析后为空: {s!r}")

    return parse_expr(t, local_dict=SYMS, transformations=TRANSFORMS, evaluate=True)


# ═══════════════════════════════════════════════════════════════════
# 结构层：矩阵
# ═══════════════════════════════════════════════════════════════════

def _split_matrix_body(body: str) -> list[list[str]]:
    r"""按 \\ 分行、& 分列。容忍行尾 \[\*pt] 之类间距参数。"""
    rows: list[list[str]] = []
    for raw_row in re.split(r"\\\\", body):
        row = re.sub(r"\[[^\]]*\]\s*$", "", raw_row.strip())
        row = row.strip()
        if not row:
            continue
        rows.append([c.strip() for c in row.split("&")])
    return rows


def parse_env_matrix(latex: str) -> list[Matrix]:
    """
    从一段 LaTeX 里抽出所有矩阵环境，返回 Matrix 列表。

    注意：_MATRIX_ENV_RE 有两个捕获组（环境前缀 p/b/v + 正文），
    而 _ARRAY_RE 只有一个（正文）。两者的正文组号不同，
    必须分开处理 —— 这也是初版把 \begin{pmatrix} 解析成 Matrix([[p]]) 的原因。
    """
    out: list[Matrix] = []

    def _build(rows: list[list[str]]):
        if not rows:
            return None
        data = [[tex_atom(c) for c in row] for row in rows]
        return Matrix(data)

    for m in _MATRIX_ENV_RE.finditer(latex):
        try:
            mat = _build(_split_matrix_body(m.group(2)))   # group2 = 正文
            if mat is not None:
                out.append(mat)
        except Exception:
            continue

    for m in _ARRAY_RE.finditer(latex):
        try:
            mat = _build(_split_matrix_body(m.group(1)))   # group1 = 正文
            if mat is not None:
                out.append(mat)
        except Exception:
            continue

    return out


def parse_all_matrices(text: str, math_exprs: list | None = None) -> list[Matrix]:
    """题目全文 + 数学表达式里搜所有矩阵。"""
    blobs = [text or ""]
    for e in (math_exprs or []):
        blobs.append(e.get("latex", "") if isinstance(e, dict) else str(e))
    mats: list[Matrix] = []
    for blob in blobs:
        mats.extend(parse_env_matrix(blob))
    return mats


_NAME_RE = re.compile(r"([A-Z])\s*(?:\^\{?T\}?|\^\\top)?\s*=\s*")


def parse_named_matrices(text: str, math_exprs: list | None = None) -> dict[str, Matrix]:
    """
    提取 "A = \\begin{pmatrix}...\\end{pmatrix}" 形式的具名矩阵。

    返回 {name: Matrix}。同名后者覆盖前者。
    """
    blobs = [text or ""]
    for e in (math_exprs or []):
        blobs.append(e.get("latex", "") if isinstance(e, dict) else str(e))

    named: dict[str, Matrix] = {}
    for blob in blobs:
        for m in _MATRIX_ENV_RE.finditer(blob):
            # 往前找最近的 NAME =
            prefix = blob[max(0, m.start() - 60):m.start()]
            name_matches = list(_NAME_RE.finditer(prefix))
            try:
                mat_candidate = parse_env_matrix(m.group(0))
                if not mat_candidate:
                    continue
            except Exception:
                continue
            if name_matches:
                name = name_matches[-1].group(1)
                named[name] = mat_candidate[0]
    return named


# ═══════════════════════════════════════════════════════════════════
# 结构层：向量列表
# ═══════════════════════════════════════════════════════════════════

def parse_column_vectors(text: str, math_exprs: list | None = None) -> list[Matrix]:
    """
    抽出所有"单列"矩阵环境，作为向量列表返回 [[v1],[v2],...]（列向量形式）。
    用于 Gram-Schmidt、线性相关性这类给一组向量的题。
    """
    mats = parse_all_matrices(text, math_exprs)
    vecs = []
    for m in mats:
        if m.cols == 1:
            vecs.append(m)
        elif m.rows == 1:
            vecs.append(m.T)      # 行向量转置成列向量统一处理
    return _dedup(vecs)


def _dedup(vs: list[Matrix]) -> list[Matrix]:
    seen, out = set(), []
    for v in vs:
        key = tuple(v.T.tolist()[0] if v.cols == 1 else [])
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def parse_tuple_vectors(text: str) -> list[Matrix]:
    """
    抽出行内写法 (1,2,3) / (1, 2, 3)^T 作为列向量。
    兜底用：有些作业不用 pmatrix 而用圆括号元组写向量。
    """
    out = []
    for m in re.finditer(r"\(\s*([-\d\./\s,]+?)\s*\)", text or ""):
        parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
        if len(parts) < 2:
            continue
        try:
            v = Matrix([tex_atom(p) for p in parts])
            out.append(v)
        except Exception:
            continue
    return out


def collect_vectors(text: str, math_exprs: list | None = None) -> list[Matrix]:
    """综合两种写法，去重后返回列向量列表。"""
    v = parse_column_vectors(text, math_exprs)
    if len(v) >= 2:
        return v
    v2 = parse_tuple_vectors(text)
    return _dedup(v + v2)


# ═══════════════════════════════════════════════════════════════════
# 结构层：方程组
# ═══════════════════════════════════════════════════════════════════

# ⚠️ 不能用 \b：x_1 里 x 和 _ 都是 \w，两者之间不存在单词边界，
#    用 \b 会导致 x_1 完全匹配不到，未知量退化成默认的 x,y,z，
#    进而 linear_eq_to_matrix(eqs, [x,y,z]) 得到全零系数矩阵 → 误判"无解"。
#    改用 lookbehind/lookahead 界定符号边界。
_UNKNOWN_RE = re.compile(r"(?<![A-Za-z])([a-z])(?:_\{?(\d+)\}?)?(?![A-Za-z0-9])")


def extract_unknowns(text: str) -> list[Symbol]:
    """
    从文本里推断未知量顺序：优先 x_1,x_2... 其次 x,y,z。
    返回按自然顺序排列的 Symbol 列表。
    """
    t = text or ""
    indexed: dict[str, list[int]] = {}
    plain: list[str] = []

    for m in _UNKNOWN_RE.finditer(t):
        base, idx = m.group(1), m.group(2)
        if idx is not None:
            indexed.setdefault(base, set()).add(int(idx))
        else:
            for b in "xyz":
                if base == b:
                    plain.append(b)

    # 优先：下标形式
    for base in ("x", "y", "z"):
        if base in indexed:
            nums = sorted(indexed[base])
            return [Symbol(f"{base}{i}") for i in nums]

    # 其次：x,y,z 连续出现
    found = [b for b in ("x", "y", "z") if b in set(plain)]
    if len(found) >= 2:
        return [Symbol(b) for b in sorted(found)]

    return [Symbol("x"), Symbol("y"), Symbol("z")]


def parse_equations(text: str, math_exprs: list | None = None) -> list[Eq]:
    """
    从 cases 环境或并列的方程里抽出等式。
    返回 [Eq(lhs, rhs), ...]，失败则空列表。
    """
    blobs = [text or ""]
    for e in (math_exprs or []):
        blobs.append(e.get("latex", "") if isinstance(e, dict) else str(e))

    eqs: list[Eq] = []
    for blob in blobs:
        # cases / array 环境
        for rx in (_CASES_RE, _ARRAY_RE):
            for m in rx.finditer(blob):
                for raw_row in re.split(r"\\\\", m.group(1)):
                    row = raw_row.strip()
                    if not row or "=" not in row:
                        continue
                    eq = _split_eq(row)
                    if eq is not None:
                        eqs.append(eq)
            if eqs:
                return eqs

        # 行形式：以 "\\" 或换行分隔的多个含等号的行
        candidates = [l for l in re.split(r"\\\\|[\n;]", blob) if "=" in l]
        if len(candidates) >= 2:
            for row in candidates:
                eq = _split_eq(row.strip())
                if eq is not None:
                    eqs.append(eq)
            if eqs:
                return eqs

        # 单个方程
        if "=" in blob:
            eq = _split_eq(blob)
            if eq is not None:
                eqs.append(eq)
    return eqs


def _split_eq(row: str):
    """把 "lhs = rhs" 变成 Eq。拒绝不等号和明显不是方程的东西。"""
    if any(bad in row for bad in ("\\ne", "\\neq", "<", ">", "\\le", "\\ge")):
        return None
    parts = row.split("=")
    if len(parts) != 2:
        return None
    try:
        lhs, rhs = tex_atom(parts[0]), tex_atom(parts[1])
        return Eq(lhs, rhs)
    except Exception:
        return None


def equations_to_Ab(eqs: list[Eq], unknowns: list[Symbol]):
    """
    把方程组化成 A x = b。

    返回 (A, b) 或 None（当某个方程不是关于未知量的线性式时）。
    """
    if not eqs or not unknowns:
        return None
    from sympy import linear_eq_to_matrix
    try:
        return linear_eq_to_matrix(eqs, unknowns)
    except Exception:
        return None


# ── 自测 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    from sympy import pprint

    t1 = r"Let $A = \begin{pmatrix} 4 & 1 \\ 2 & 3 \end{pmatrix}$ and $B = \begin{bmatrix} 0 & -1 \\ 1 & 0 \end{bmatrix}$."
    print("named:", {k: v.tolist() for k, v in parse_named_matrices(t1).items()})

    t2 = r"""
    Solve:
    $$\begin{cases}
    x_1 + 2x_2 - x_3 = 1 \\
    2x_1 - x_2 + 3x_3 = 7 \\
    3x_1 + x_2 + 2x_3 = 4
    \end{cases}$$
    """
    unk = extract_unknowns(t2)
    eqs = parse_equations(t2)
    print("unknowns:", unk, "| n_eqs:", len(eqs))
    Ab = equations_to_Ab(eqs, unk)
    if Ab:
        pprint(Ab[0]); pprint(Ab[1])

    t3 = r"""Orthogonalize $v_1 = \begin{pmatrix}1\\1\\0\end{pmatrix}$,
             $v_2 = \begin{pmatrix}1\\0\\1\end{pmatrix}$,
             $v_3 = \begin{pmatrix}0\\1\\1\end{pmatrix}$."""
    print("vectors:", [v.T.tolist() for v in collect_vectors(t3)])

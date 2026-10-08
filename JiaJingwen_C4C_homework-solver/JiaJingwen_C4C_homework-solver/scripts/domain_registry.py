#!/usr/bin/env python3
"""
多领域 T-box 注册表
=====================================================================

Claude 基线的结构性限制
--------------------------------------------------------------------
看 baseline 的 classify.py：

    tbox_path = SKILL_ROOT / "domain_skills" / "calculus_limits.yaml"

domain 被**硬编码**为一个文件名。这意味着：
  · 加一门新学科 = 改 classify.py / retrieve.py 的路径常量
  · 所有 domain 的 classification_rules 无法共存于一个优先级队列里
  · 概念 ID 跨 domain 冲突时（例如 limits 和 linalg 都有 "identity"）无隔离机制

换句话说，Claude 版虽然嘴上说 "Solve rate = domain coverage"，
但它的架构里 domain 是单数。这是"想扩展很难"的真正原因。

本模块的改造
--------------------------------------------------------------------
1. **Domain Registry**：扫描 domain_skills/*.yaml，每个 domain 一个 namespace
2. **合并优先级队列**：所有 domain 的 rules 合成一个 (priority, domain) 队列，
   按 priority 全局降序匹配 —— 优先级跨领域可比
3. **ID 隔离**：内部以 `{domain}::{concept_id}` 存储，避免跨界污染；
   对外仍返回原始 id 以保持兼容
4. **可解释性**：分类时记录命中了哪个 domain 的哪条 rule 及 reason，
   验证报告里可以直接回答"为什么这题被判成特征值问题"

Usage:
    reg = get_registry()
    res = reg.classify(problem)      # Classification 对象
    print(res.domain, res.concept_id, res.solver_id, res.reason)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

try:
    from bootstrap import SKILL_ROOT
except ImportError:  # pragma: no cover
    SKILL_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Classification:
    """一次分类决策的完整记录 —— 用于可解释性与复盘。"""
    domain: Optional[str] = None
    concept_id: Optional[str] = None
    solver_id: Optional[str] = None
    priority: int = 0
    reason: str = "no match"
    rule_id: Optional[str] = None
    features: dict = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return self.concept_id is not None or self.solver_id is not None


@dataclass
class Domain:
    """单个学科领域的完整 T-box。"""
    domain_id: str
    path: Path
    meta: dict
    concepts: dict
    solution_methods: dict
    rules: list
    validation_rules: list
    concepts_by_id: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.meta.get("display_name", self.meta.get("domain", self.domain_id))


# ═══════════════════════════════════════════════════════════════════
# 特征提取 —— 观察层
# ═══════════════════════════════════════════════════════════════════

class FeatureExtractor:
    """
    把一道题变成一组"可被规则匹配的特征"。

    这是 klassifier 的观察层：它只描述**看得见的事实**
    （文本里有没有这个词、有没有矩阵、有没有方程组），
    不做任何领域推断。推断是 T-box 规则的事。

    与 Claude 基线相比新增的特征（线性代数必需）：
      has_matrix / matrix_count       — LaTeX pmatrix/bmatrix/array
      has_linear_system               — cases 环境包裹的方程组
      has_column_vector               — \\begin{pmatrix} 里只有一列
      has_eigen Keyword               — eigen / characteristic polynomial
      text_regexp (可选扩展)          — 允许 YAML 里写正则
    """

    MATRIX_ENVS = ("pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "matrix", "array")

    def extract(self, problem: dict) -> dict:
        text = problem.get("text", "") or ""
        math_exprs = problem.get("math_expressions") or []
        subs = problem.get("sub_problems") or []

        sub_texts = [s.get("text", "") if isinstance(s, dict) else str(s) for s in subs]
        sub_math: list = []
        for s in subs:
            if isinstance(s, dict):
                sub_math.extend(s.get("math_expressions") or [])

        all_text = " ".join([text] + sub_texts)
        all_latex = [e.get("latex", "") if isinstance(e, dict) else str(e)
                     for e in (list(math_exprs) + sub_math)]

        return {
            "text_raw": text,
            "text_lower": all_text.lower(),
            "latex_strs": [e.get("latex", "") if isinstance(e, dict) else str(e)
                           for e in math_exprs],
            "sub_latex": [str(e) for e in sub_math],
            "all_latex": all_latex,
            "has_function_definition": self._has_function_definition(math_exprs),
            "has_abstract_function": self._has_abstract_function(all_latex),
            "has_limit_expression": self._has_limit(all_latex),
            "has_matrix": self._has_matrix(all_latex),
            "matrix_count": self._matrix_count(all_latex),
            "has_linear_system": self._has_linear_system(all_text, all_latex),
            "has_column_vector": self._has_column_vector(all_latex),
            "has_sub_problems": len(subs) > 0,
            "sub_count": len(subs),
        }

    # ── 既有特征 ───────────────────────────────
    @staticmethod
    def _has_function_definition(math_exprs) -> bool:
        for e in math_exprs:
            s = e.get("latex", "") if isinstance(e, dict) else str(e)
            if re.match(r"f\s*\(\s*x\s*\)\s*=\s*.+", s):
                return True
            m = re.match(r"y\s*=\s*(.+)", s)
            if m and not re.search(r"[fgh]\s*\(", m.group(1)):
                return True
        return False

    @staticmethod
    def _has_abstract_function(latex_list) -> bool:
        return any(re.search(r"[fgh]\s*\([a-z]\)", s) for s in latex_list)

    @staticmethod
    def _has_limit(latex_list) -> bool:
        return any(("\\lim" in s or "lim_" in s) for s in latex_list)

    # ── 线性代数新增特征 ────────────────────────
    @classmethod
    def _has_matrix(cls, latex_list) -> bool:
        return cls._matrix_count(latex_list) > 0

    @classmethod
    def _matrix_count(cls, latex_list) -> int:
        n = 0
        for s in latex_list:
            for env in cls.MATRIX_ENVS:
                n += len(re.findall(r"\\begin\{" + env + r"\*?\}", s))
        return n

    @staticmethod
    def _has_linear_system(text: str, latex_list) -> bool:
        """cases 环境 或 多个含 x1,x2 的方程并列。"""
        if any("\\begin{cases}" in s or "\\begin{array}" in s for s in latex_list):
            return True
        # 文本里出现 >=2 个 x_{1}/x1 类未知量，且有等号
        if "=" in " ".join(latex_list) or "=" in text:
            hits = len(set(re.findall(r"(?:x_?\{?[123]\}?|y_?\{?[123]\}?|z_?\{?[123]\}?)", text)))
            if hits >= 2:
                return True
        return False

    @staticmethod
    def _has_column_vector(latex_list) -> bool:
        for s in latex_list:
            m = re.search(r"\\begin\{(p|b)matrix\*?\}(.*?)\\end\{\1matrix\*?\}", s, re.DOTALL)
            if not m:
                continue
            body = m.group(2)
            rows = [r for r in body.split("\\\\") if r.strip()]
            if rows and all("&" not in r for r in rows):
                return True
        return False


# ═══════════════════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════════════════

class DomainRegistry:
    """
    加载并合并 domain_skills/ 下所有 YAML，对外提供跨领域分类。
    """

    def __init__(self, domains_dir: Path | str | None = None, verbose: bool = False):
        self.domains_dir = Path(domains_dir) if domains_dir else (SKILL_ROOT / "domain_skills")
        self.extractor = FeatureExtractor()
        self.domains: dict[str, Domain] = {}
        self._queue: list[tuple[int, str, dict]] = []   # (priority, domain_id, rule)
        self.verbose = verbose
        self._load_all()

    # ── 加载 ────────────────────────────────────
    def _load_all(self):
        if not self.domains_dir.exists():
            return
        for path in sorted(self.domains_dir.glob("*.yaml")):
            self._load_one(path)
        # 全局优先级队列（降序）：跨 domain 可比
        self._queue.sort(key=lambda t: t[0], reverse=True)
        if self.verbose:
            print(f"  [Registry] 已加载 {len(self.domains)} 个领域, "
                  f"共 {len(self._queue)} 条分类规则")

    def _load_one(self, path: Path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"  [Registry] ⚠️ 无法解析 {path.name}: {e}")
            return

        domain_id = data.get("domain") or path.stem
        concepts = {c["id"]: c for c in (data.get("concepts") or []) if "id" in c}
        methods = {m["id"]: m for m in (data.get("solution_methods") or []) if "id" in m}
        rules = data.get("classification_rules") or []

        self.domains[domain_id] = Domain(
            domain_id=domain_id,
            path=path,
            meta=data,
            concepts=concepts,
            solution_methods=methods,
            rules=rules,
            validation_rules=data.get("validation_rules") or [],
            concepts_by_id=concepts,
        )

        for i, rule in enumerate(rules):
            self._queue.append((int(rule.get("priority", 0)), domain_id,
                                {**rule, "_rule_id": f"{domain_id}#{i}"}))

    # ── 分类 ────────────────────────────────────
    def classify(self, problem: dict) -> Classification:
        """
        跨所有领域按全局优先级匹配第一条命中的规则。
        """
        features = self.extractor.extract(problem)

        for priority, domain_id, rule in self._queue:
            if self._rule_matches(rule, features):
                return Classification(
                    domain=domain_id,
                    concept_id=rule.get("maps_to"),
                    solver_id=rule.get("solver"),
                    priority=priority,
                    reason=rule.get("reason", f"命中 {domain_id} 优先级 {priority} 规则"),
                    rule_id=rule.get("_rule_id"),
                    features=features,
                )

        return Classification(domain=None, reason="没有任何领域的规则命中",
                              features=features)

    def classify_all_matches(self, problem: dict) -> list[Classification]:
        """返回所有命中的规则（按优先级降序）—— 用于调试/交叉验证。"""
        features = self.extractor.extract(problem)
        out = []
        for priority, domain_id, rule in self._queue:
            if self._rule_matches(rule, features):
                out.append(Classification(
                    domain=domain_id, concept_id=rule.get("maps_to"),
                    solver_id=rule.get("solver"), priority=priority,
                    reason=rule.get("reason", ""), rule_id=rule.get("_rule_id"),
                    features=features))
        return out

    # ── 规则匹配 ────────────────────────────────
    def _rule_matches(self, rule: dict, features: dict) -> bool:
        pattern = rule.get("pattern") or {}
        for key, value in pattern.items():
            if not self._check(key, value, features):
                return False
        return True

    def _check(self, key: str, value, features: dict) -> bool:
        if key == "text_contains":
            tl = features["text_lower"]
            return any(str(kw).lower() in tl for kw in value)

        if key == "latex_contains":
            joined = " ".join(features["all_latex"])
            return any(str(kw) in joined for kw in value)

        if key == "text_regexp":
            joined = features["text_raw"] + " " + " ".join(features["all_latex"])
            return any(re.search(p, joined) for p in value)

        if key in features:
            return features[key] == value

        # 未知条件一律不匹配（安全默认，与 Claude 基线一致）
        return False

    # ── 查询辅助 ────────────────────────────────
    def get_solver_method(self, solver_id: str) -> Optional[dict]:
        for d in self.domains.values():
            if solver_id in d.solution_methods:
                return d.solution_methods[solver_id]
        return None

    def get_concept(self, concept_id: str) -> Optional[dict]:
        for d in self.domains.values():
            if concept_id in d.concepts:
                return d.concepts[concept_id]
        return None

    def domains_for(self, concept_id: str) -> list[str]:
        return [d for d, dom in self.domains.items() if concept_id in dom.concepts]

    def summary(self) -> str:
        lines = []
        for did, d in self.domains.items():
            lines.append(f"  · {did}: {len(d.concepts)} 概念, "
                         f"{len(d.solution_methods)} 解法, {len(d.rules)} 规则")
        return "\n".join(lines)


# ── 进程内单例 ─────────────────────────────────────────────────────
_REGISTRY: DomainRegistry | None = None


def get_registry(verbose: bool = False, force_reload: bool = False) -> DomainRegistry:
    global _REGISTRY
    if _REGISTRY is None or force_reload:
        _REGISTRY = DomainRegistry(verbose=verbose)
    return _REGISTRY


# ── legacy 桥接：domain concept → solve.py 的 SOLVERS key ────────────
# 沿用 Claude 基线的 SOLVER_TYPE_MAP 思路，但按 domain 分开维护，避免冲突。
SOLVER_TYPE_MAP = {
    # linalg
    "determinant_solver": "matrix_determinant",
    "inverse_solver": "matrix_inverse",
    "rank_solver": "matrix_rank",
    "trace_transpose_solver": "matrix_trace_transpose",
    "eigen_solver": "matrix_eigen",
    "diagonalize_solver": "matrix_diagonalize",
    "linear_system_solver": "matrix_linear_system",
    "gram_schmidt_solver": "matrix_gram_schmidt",
    "linear_independence_solver": "matrix_independence",
    "basis_dimension_solver": "matrix_basis_dimension",
    "matrix_arithmetic_solver": "matrix_arithmetic",
    "quadratic_form_solver": "matrix_quadratic_form",
    # calculus (保持与基线一致)
    "ed_notation_solver": "epsilon_delta",
    "ed_computation_solver": "epsilon_delta",
    "horizontal_tangent_solver": "tangent",
    "tangent_at_point_solver": "tangent",
    "limit_direct_computation": "limit",
    "limit_dne_proof": "limit",
    "limit_squeeze": "limit",
    "conceptual_limit_definitions": "conceptual",
    "conceptual_tangent_questions": "conceptual",
    "lhopital_solver": "limit",
}

CONCEPT_TYPE_MAP = {
    # linalg
    "matrix": "matrix_arithmetic",
    "determinant": "matrix_determinant",
    "matrix_inverse": "matrix_inverse",
    "matrix_rank": "matrix_rank",
    "trace": "matrix_trace_transpose",
    "transpose": "matrix_trace_transpose",
    "eigenvalue": "matrix_eigen",
    "eigenvector": "matrix_eigen",
    "diagonalization": "matrix_diagonalize",
    "linear_system": "matrix_linear_system",
    "gram_schmidt": "matrix_gram_schmidt",
    "orthogonalization": "matrix_gram_schmidt",
    "linear_independence": "matrix_independence",
    "basis": "matrix_basis_dimension",
    "quadratic_form": "matrix_quadratic_form",
    # calculus (保持与基线一致)
    "epsilon_delta_notation": "epsilon_delta",
    "epsilon_delta_computation": "epsilon_delta",
    "horizontal_tangent": "tangent",
    "tangent_line": "tangent",
    "limit_definition": "limit",
    "limit_existence": "limit",
    "one_sided_limit": "limit",
    "squeeze_theorem": "limit",
    "continuity": "conceptual",
    "infinity_limit": "limit",
    "lhopital_rule": "limit",
    "discontinuity_types": "conceptual",
    "intermediate_value_theorem": "conceptual",
    "growth_hierarchy": "conceptual",
}


def classify_to_type(problem: dict, registry: DomainRegistry = None) -> tuple[str, Classification]:
    """
    分类并映射成 solve.py 的 legacy type 字符串。

    返回 (type_str, Classification)，Classification 里保留了
    domain / reason / rule_id，供验证报告溯源。
    """
    reg = registry or get_registry()
    c = reg.classify(problem)

    if c.solver_id and c.solver_id in SOLVER_TYPE_MAP:
        return SOLVER_TYPE_MAP[c.solver_id], c
    if c.concept_id and c.concept_id in CONCEPT_TYPE_MAP:
        return CONCEPT_TYPE_MAP[c.concept_id], c

    # 未命中走 T-box 之外的经验回退：先看关键词，再兜底 conceptual
    text = (problem.get("text", "") or "").lower()
    for kw, t in (("determinant", "matrix_determinant"),
                  ("行列式", "matrix_determinant"),
                  ("eigenvalue", "matrix_eigen"),
                  ("特征值", "matrix_eigen"),
                  ("eigenvector", "matrix_eigen"),
                  ("特征向量", "matrix_eigen"),
                  ("inverse", "matrix_inverse"),
                  ("逆矩阵", "matrix_inverse"),
                  ("rank", "matrix_rank"),
                  ("秩", "matrix_rank")):
        if kw in text:
            c.solver_id, c.reason = t, f"关键词回退: {kw}"
            return t, c

    return "conceptual", c


if __name__ == "__main__":
    reg = get_registry(verbose=True)
    print(reg.summary())
    demo = {
        "text": "Find the eigenvalues of the matrix",
        "math_expressions": [{"latex": r"A = \begin{pmatrix} 4 & 1 \\ 2 & 3 \end{pmatrix}"}],
        "sub_problems": [],
    }
    t, c = classify_to_type(demo, reg)
    print(f"\n demo → type={t} domain={c.domain} concept={c.concept_id} "
          f"solver={c.solver_id}\n reason={c.reason}")

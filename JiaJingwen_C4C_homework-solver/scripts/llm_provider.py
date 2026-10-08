#!/usr/bin/env python3
"""
Tier-2 引擎：国产大模型 Provider 适配层
=========================================

这是 C4C 迁移工作的核心 —— 把 starter kit 里"必须由 Claude Code 这个 Agent
来做推理"的部分，抽成一个**可替换的 LLM 后端**。

为什么这样设计？
------------------------------------------------------------------
Claude 基线的隐藏耦合：

    solve.py 遇到做不了的题 → 返回 "需要 LLM 求解器（学生扩展点）"
                                        ↑ 这个 LLM = 运行管道的 Agent 自己

也就是说，Claude 版的"LLM 引擎"其实是 **运行时的 Agent 本体**，不是代码里的
一个函数。这导致两个问题：
  1. 换引擎 = 换整个运行环境（无法在同一进程里对比 Qwen / DeepSeek / Kimi）
  2. 没有引擎就完全降级为 0 推理能力

本模块把 LLM 显式化为一个 Provider 对象，定义统一契约：

    provider.complete(system, user, **kw) -> str
    provider.solve_json(system, user, schema_hint) -> dict

于是：
  · DeepSeek / Qwen / Kimi 都是 OpenAI 兼容协议 → 一个基类通吃
  · Claude 是 Anthropic 协议 → 单独适配器（用于横向对比）
  · 没有 key → NullProvider，返回结构化 "unavailable"，管道自动降级到 SymPy + 模板

关键设计决策
------------------------------------------------------------------
1. **引擎无关的下游**
   solve.py 只依赖 `problem_solver()` 返回的结构，不关心背后是 DeepSeek 还是模板。
   这样"换模型"不需要改一行业务逻辑 —— 这正是"迁移"的正确粒度。

2. **显式降级而非静默失败**
   NullProvider 不会假装能做题。它返回 `{"status": "unavailable", ...}`，
   调用方必须显式处理。这比"随机输出一个答案"安全得多。

3. **磁盘缓存**
   同样 (provider, model, prompt) 的结果缓存到本地。作用有三：
   · 省钱（重跑流水线不重复计费）
   · 可复现（验证报告的数字可以被别人复跑出来）
   · 离线演示（无 key 时也能展示之前跑过的 LLM 输出）

Usage:
    from llm_provider import get_provider
    p = get_provider()                 # 读 LLM_PROVIDER 环境变量，默认 deepseek
    print(p.available)                 # False 表示无 key，将降级
    ans = p.solve_json(SYS, USR, "...")
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

try:
    from bootstrap import SKILL_ROOT
except ImportError:  # pragma: no cover
    SKILL_ROOT = Path(__file__).resolve().parent.parent

# ── 轻量 .env 读取（不引入 python-dotenv 依赖） ───────────────────────────
def _load_dotenv() -> None:
    """把 skill 根目录 .env 里的 KEY=VALUE 塞进 os.environ（已存在的不覆盖）。"""
    env_path = Path(SKILL_ROOT) / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


# ═══════════════════════════════════════════════════════════════════
# Provider 契约
# ═══════════════════════════════════════════════════════════════════

class LLMProvider(ABC):
    """所有 LLM 后端的统一接口。"""

    name = "abstract"
    model = ""
    # 是否支持 response_format={"type":"json_object"}
    supports_json_mode = False

    def __init__(self, model: str | None = None, timeout: int = 120):
        self.timeout = timeout
        if model:
            self.model = model
        self._cache_dir = Path(SKILL_ROOT) / ".llm_cache"
        self.last_error: str | None = None
        # 本次进程内的调用统计，供"AI 日志 / 用量"使用
        self.stats = {"calls": 0, "cache_hits": 0, "prompt_tokens": 0,
                      "completion_tokens": 0, "errors": 0}

    # ── 子类实现 ──────────────────────────────────
    @property
    @abstractmethod
    def available(self) -> bool:
        """是否有可用凭据/网络。False → 调用方应走降级路径。"""

    @abstractmethod
    def _raw_complete(self, system: str, user: str, temperature: float,
                      max_tokens: int, json_mode: bool) -> tuple[str, dict]:
        """真正发请求。返回 (text, usage_dict)。"""

    # ── 通用层 ────────────────────────────────────
    def _cache_key(self, system: str, user: str, temperature: float,
                   max_tokens: int, json_mode: bool) -> str:
        payload = f"{self.name}|{self.model}|{temperature}|{max_tokens}|{json_mode}|{system}|{user}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def _cache_get(self, key: str) -> str | None:
        f = self._cache_dir / f"{key}.txt"
        if f.exists():
            return f.read_text(encoding="utf-8")
        return None

    def _cache_put(self, key: str, value: str) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            (self._cache_dir / f"{key}.txt").write_text(value, encoding="utf-8")
        except Exception:
            pass  # 缓存失败不影响主流程

    def complete(self, system: str, user: str, temperature: float = 0.0,
                 max_tokens: int = 2048, json_mode: bool = False,
                 use_cache: bool = True) -> str:
        """带缓存 + 错误吞掉的完整调用。失败返回 ""。"""
        if not self.available:
            self.stats["errors"] += 1
            return ""

        key = self._cache_key(system, user, temperature, max_tokens, json_mode)
        if use_cache:
            hit = self._cache_get(key)
            if hit is not None:
                self.stats["cache_hits"] += 1
                self.stats["calls"] += 1
                return hit

        try:
            text, usage = self._raw_complete(system, user, temperature,
                                             max_tokens, json_mode)
        except Exception as e:  # 网络/鉴权/限流一律降级，不炸管道
            self.last_error = f"{type(e).__name__}: {e}"
            self.stats["errors"] += 1
            return ""

        self.stats["calls"] += 1
        self.stats["prompt_tokens"] += usage.get("prompt_tokens", 0)
        self.stats["completion_tokens"] += usage.get("completion_tokens", 0)

        if text and use_cache:
            self._cache_put(key, text)
        return text

    # ── 结构化输出 ────────────────────────────────
    def solve_json(self, system: str, user: str, schema_hint: str = "",
                   **kw) -> dict:
        """
        要求模型输出 JSON，并尽最大努力把它解析成 dict。

        三层防御：
          1. 先用 provider 原生 json_mode（如果支持）
          2. 再在 prompt 里硬性规定 schema
          3. 最后用正则从文本里捞第一个 {...} 块

        永远返回 dict，绝不抛异常。
        """
        if schema_hint:
            user = f"{user}\n\n请严格按照以下 JSON schema 输出，不要输出任何解释性文字：\n{schema_hint}"

        text = self.complete(system, user, json_mode=self.supports_json_mode, **kw)

        if not text:
            return {"status": "unavailable",
                    "reason": self.last_error or f"{self.name} 不可用（无 API key 或调用失败）"}

        obj = _extract_json(text)
        if obj is None:
            return {"status": "unparsable", "raw": text[:2000],
                    "reason": "模型输出无法解析为 JSON"}
        obj.setdefault("status", "ok")
        return obj


def _extract_json(text: str) -> dict | None:
    """从可能带 markdown 代码围栏的文本里.extract 出 JSON 对象。"""
    if not text:
        return None
    t = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    # 直接整体解析
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # 捞第一个balanced的 {...}
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass
                break
    return None


# ═══════════════════════════════════════════════════════════════════
# OpenAI 兼容协议 —— DeepSeek / 通义千问 / Kimi 共用
# ═══════════════════════════════════════════════════════════════════

class OpenAICompatProvider(LLMProvider):
    """
    覆盖国内三家主流模型的 OpenAI 兼容端点。

    DeepSeek  : https://api.deepseek.com            deepseek-chat / deepseek-reasoner
    通义千问  : https://dashscope.aliyuncs.com/compatible-mode/v1   qwen-plus / qwen-max
    Kimi      : https://api.moonshot.cn/v1          moonshot-v1-8k / moonshot-v1-128k

    三者都吃 `POST /chat/completions` + Bearer token，因此一个类即可，
    差异只在 base_url / 默认模型 / 环境变量名。
    """

    supports_json_mode = True

    def __init__(self, api_key: str | None = None, model: str | None = None, **kw):
        super().__init__(model=model, **kw)
        self.api_key = api_key or os.getenv(self.env_key, "").strip()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _raw_complete(self, system, user, temperature, max_tokens, json_mode):
        import urllib.request
        import urllib.error

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode and self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:500]
            raise RuntimeError(f"HTTP {e.code}: {body}") from None

        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise RuntimeError(f"未预期的响应结构: {str(data)[:300]}")

        usage = data.get("usage", {}) or {}
        return text, {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        }


class DeepSeekProvider(OpenAICompatProvider):
    """主引擎：DeepSeek。deepseek-chat 通用，deepseek-reasoner 用于证明/多步推理。"""
    name = "deepseek"
    env_key = "DEEPSEEK_API_KEY"
    base_url = "https://api.deepseek.com"
    model = "deepseek-chat"

    def __init__(self, api_key=None, model=None, **kw):
        super().__init__(api_key=api_key, model=model or os.getenv("DEEPSEEK_MODEL"), **kw)


class QwenProvider(OpenAICompatProvider):
    """通义千问（DashScope 兼容模式），用于横向对比。"""
    name = "qwen"
    env_key = "DASHSCOPE_API_KEY"
    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = "qwen-plus"


class KimiProvider(OpenAICompatProvider):
    """Moonshot Kimi，用于横向对比（长上下文优势）。"""
    name = "kimi"
    env_key = "MOONSHOT_API_KEY"
    base_url = "https://api.moonshot.cn/v1"
    model = "moonshot-v1-32k"


# ═══════════════════════════════════════════════════════════════════
# Anthropic 协议 —— Claude 基线（仅用于对比，非主引擎）
# ═══════════════════════════════════════════════════════════════════

class ClaudeProvider(LLMProvider):
    """Anthropic Messages API。保留它只为做「国产 vs Claude」的同题对比。"""
    name = "claude"
    env_key = "ANTHROPIC_API_KEY"
    supports_json_mode = False
    model = "claude-sonnet-4-20250514"

    def __init__(self, api_key=None, model=None, **kw):
        super().__init__(model=model, **kw)
        self.api_key = api_key or os.getenv(self.env_key, "").strip()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _raw_complete(self, system, user, temperature, max_tokens, json_mode):
        import urllib.request
        import urllib.error

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:500]
            raise RuntimeError(f"HTTP {e.code}: {body}") from None

        try:
            parts = data.get("content", [])
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        except Exception:
            raise RuntimeError(f"未预期的响应结构: {str(data)[:300]}")

        usage = data.get("usage", {}) or {}
        return text, {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        }


# ═══════════════════════════════════════════════════════════════════
# 离线降级
# ═══════════════════════════════════════════════════════════════════

class NullProvider(LLMProvider):
    """
    没有 API key 时的显式降级后端。

    这不是"假装会做题"，而是诚实地告诉调用方：推理引擎不可用，
    请只用确定性通道（SymPy + 模板）。solve.py 据此把题目标为
    "需要 LLM" 而不是编一个答案出来。
    """
    name = "none"
    model = "-"
    supports_json_mode = False

    @property
    def available(self) -> bool:
        return False

    def _raw_complete(self, system, user, temperature, max_tokens, json_mode):
        raise RuntimeError("NullProvider 不可用（未配置任何 LLM API key）")

    def solve_json(self, system, user, schema_hint="", **kw):
        return {"status": "unavailable",
                "reason": "未配置 LLM_API（离线模式）：推理题需配置 API key 后启用"}


# ═══════════════════════════════════════════════════════════════════
# 工厂 + 提示词
# ═══════════════════════════════════════════════════════════════════

REGISTRY = {
    "deepseek": DeepSeekProvider,
    "qwen": QwenProvider,
    "kimi": KimiProvider,
    "claude": ClaudeProvider,
    "none": NullProvider,
    "offline": NullProvider,
}

_PROVIDER_SINGLETON: dict[str, LLMProvider] = {}


def get_provider(name: str | None = None, model: str | None = None) -> LLMProvider:
    """
    获取 provider 实例（进程内单例，便于统计用量）。

    优先级: 参数 > 环境变量 LLM_PROVIDER > 自动探测第一个有 key 的国产模型 > Null
    """
    if name is None:
        name = os.getenv("LLM_PROVIDER", "").strip() or None

    if name is None:
        # 自动探测：按国产模型顺序找第一个配置了 key 的
        for cand in ("deepseek", "qwen", "kimi"):
            if os.getenv(REGISTRY[cand].env_key, "").strip():
                name = cand
                break
        else:
            name = "none"

    key = f"{name}:{model or ''}"
    if key in _PROVIDER_SINGLETON:
        return _PROVIDER_SINGLETON[key]

    cls = REGISTRY.get(name, NullProvider)
    prov = cls(model=model)
    _PROVIDER_SINGLETON[key] = prov
    return prov


# ── 提示词（中英分离，按作业语言选用） ─────────────────────────────

SOLVER_SCHEMA = """{
  "solved": true 或 false,
  "steps": ["解题步骤1（可含 LaTeX）", "解题步骤2", ...],
  "answer_latex": "最终答案的 LaTeX（纯公式，不含 $ 符号）",
  "answer_text": "最终答案的自然语言描述",
  "confidence": 0.0 到 1.0 之间的数,
  "method": "用到的主要定理或方法名"
}"""

SOLVER_SYSTEM_ZH = """你是一位严谨的数学助教，负责求解数学题并给出清晰的解题步骤。

规则（必须遵守）：
1. 所有数学符号用 LaTeX 表示，例如 \\frac{a}{b}、\\lambda^{2}、\\begin{pmatrix}1&2\\\\3&4\\end{pmatrix}。
2. steps 数组中每一步必须是一个完整的、可独立读懂的句子或公式。
3. answer_latex 只包含最终答案的数学表达式本身，不要包裹 $ 符号，不要写"答案是"。
4. 如果题目条件不足或有歧义无法求解，把 solved 设为 false，并在 answer_text 里说明缺什么。
5. 不要编造题目没有给出的数值。宁可说无解，也不要猜。
6. 计算必须精确。特征向量可以给最简整数比或 normalize 后的形式，并注明。"""

SOLVER_SYSTEM_EN = """You are a rigorous mathematics TA. Solve the given problem and provide clear steps.

Rules (must follow):
1. Use LaTeX for all math notation.
2. Each element of "steps" must be a complete, self-contained sentence or equation.
3. "answer_latex" contains ONLY the final mathematical expression — no $ delimiters, no prose.
4. If the problem is underspecified or ambiguous, set "solved" to false and explain what is missing.
5. Never invent numbers that are not given. Prefer "unsolvable" over guessing.
6. Be exact. Eigenvectors may be given as simplest integer ratios or normalized, but say which."""


def build_solver_prompt(problem_text: str, lang: str = "zh",
                        domain_hint: str = "") -> tuple[str, str]:
    """构造 (system, user) 提示词对。"""
    system = SOLVER_SYSTEM_ZH if lang == "zh" else SOLVER_SYSTEM_EN
    if domain_hint:
        system += f"\n\n本题所属领域: {domain_hint}"
    user = f"题目：\n{problem_text}\n\n请求解并按要求输出 JSON。"
    return system, user


# ── 自测 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    for nm in ("deepseek", "qwen", "kimi", "claude", "none"):
        p = get_provider(nm)
        print(f"{nm:10s} available={str(p.available):5s} model={p.model}")
    p = get_provider()
    print(f"\n默认引擎: {p.name} (available={p.available})")
    print("solve_json 降级输出:", p.solve_json("sys", "usr"))

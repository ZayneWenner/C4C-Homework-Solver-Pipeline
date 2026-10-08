# JiaJingwen_C4C_拿来说明

> 从 starter kit 拿了什么、用了哪些库、Claude 版与我的版本有何差异
> 作者：贾静文 Jia Jingwen

---

## 一、从 starter kit 拿了什么（保留原样）

我是**复制整个 starter kit 目录**开始改的（原目录 `c4c-homework-solver-starter/` 完整保留，
用于回归对照）。以下部分我基本没动：

| 拿来什么 | 原文件 | 我怎么用的 |
|---|---|---|
| **5 Stage 流水线骨架** | `scripts/pipeline.py` | 保留 Stage 划分与 JSON 中间产物约定 |
| **微积分极限本体** | `domain_skills/calculus_limits.yaml` | 原样保留，这是 17/18 的来源 |
| **T-box 分类器** | `scripts/classify.py` | 保留规则匹配逻辑，替换了它的**单域加载** |
| **解法检索** | `scripts/retrieve.py` | 保留 |
| **LaTeX 作业模板** | `references/homework_template.tex` | 保留"题目+解答交替排版"的设计 |
| **测试用例** | `test_cases/test1_*.md`, `test2_*.md` | 用作回归基线 |
| **SymPy 求解器** | `scripts/solve.py` 的微积分部分 | 保留，新增线代路由 |

### 为什么要整目录复制而不是 fork 式修改

因为我要**随时能跑原版做对照**。
每次我改完，都会重跑原测试集验证"没弄坏" —— 这需要一个原封不动的参照物。
最后结果是：微积分 17/18 与基线**分毫不差**。

---

## 二、拿了之后改了什么（重写 / 新增）

### 2.1 重写：破解单域限制

**原版 `classify.py` 的核心限制：**

```python
_tbox_classifier = TBoxClassifier()   # 只能加载一个 YAML
```

这意味着即使写好 5 个学科的本体，系统也只认第一个。
**"扩展学科"在这个架构下不是内容问题，是架构问题。**

→ 新增 `scripts/domain_registry.py`：自动扫描 `domain_skills/*.yaml` 并按优先级注册。
现在一次加载 2 个领域、33 条分类规则；加第三个学科只需丢一个 YAML 进去。

### 2.2 重写：让解析器能读线性代数

**原版 `parse_problems.py` / 数学抽取的问题：**
会把 `x_1` 的下标吃掉（`x_1` → `x`），线性方程组直接全废。

→ 新增 `scripts/latex_matrix.py`：
- `\begin{bmatrix}` / `pmatrix` / `array` → SymPy `Matrix`
- `\begin{cases}` 方程组 → `A·x = b` 的系数矩阵 + 右端向量
- 未知量提取 `x_1, x_2, …`（修掉了 `\b` 单词边界的坑，见 AI 日志）

### 2.3 新增：Stage 3.5 答案验证

**原版的一个性质我无法接受：`solved=True` 只表示"没崩"，不表示"对"。**

→ 新增 `scripts/verify.py`，7 类恒等式：

| 验证律 | 检查 |
|---|---|
| `det_inverse` | A·A⁻¹ = I |
| `eigen` | A·v = λ·v |
| `diagonalize` | P⁻¹AP = Λ |
| `matrix_rank` | 阶梯形主元行数 = 秩 |
| `system_residual` | Ax − b = 0 |
| `gram_schmidt` | eᵢ·eⱼ = 0，‖eᵢ‖ = 1 |
| `quadratic_form` | 顺序主子式符号与定性一致 |

验证结果渲染进 PDF：「■ 答案已通过数学恒等式自检（2/2）」。

### 2.4 新增：国产模型适配层

→ 新增 `scripts/llm_provider.py`：

```python
LLMProvider(ABC)
  ├─ OpenAICompatProvider
  │    ├─ QwenProvider      通义千问（dashscope）
  │    ├─ DeepSeekProvider  DeepSeek
  │    └─ KimiProvider      Moonshot
  ├─ ClaudeProvider         Anthropic 原生协议
  └─ NullProvider           无 key 时降级
```

换引擎 = 改 `.env` 一行，代码零改动。**但当前无 key，未实测。**

### 2.5 补完：starter kit 留的空壳

原版这三个函数是**故意留给学生做的**，内容是 `raise NotImplementedError`：

```python
def read_pdf_text(filepath):   raise NotImplementedError("PDF 摄入未实现…")
def read_docx(filepath):       raise NotImplementedError("Word 摄入未实现…")
def read_image_ocr(filepath):  raise NotImplementedError("OCR 摄入未实现…")
```

→ 我都实现了（`scripts/ingest.py`）：

| 格式 | 实现 | 依赖 |
|---|---|---|
| PDF 文本型 | `pdfplumber` 逐页 `extract_text()` | pdfplumber |
| Word | `python-docx`，段落 + 表格 | python-docx |
| 图片 | Tesseract OCR（需自备） | pytesseract + Pillow |

### 2.6 重写：渲染与编译

`render_latex.py` 我基本重写了，保留模板骨架，改了三处：

| 改了什么 | 为什么 |
|---|---|
| 中文支持（xeCJK / ctex） | 原版模板不支持中文，作业是中文的 |
| 渲染验证结果 | 配合 Stage 3.5 |
| **自动探测 LaTeX 引擎** | 原版硬编码，找不到就崩 |

---

## 三、用了哪些外部库

| 库 | 用途 | 来源 |
|---|---|---|
| **SymPy** | 符号计算主引擎 | starter kit 已指定，沿用 |
| **PyYAML** | 解析领域本体 YAML | 沿用 |
| **pdfplumber** | PDF 文本提取 | ★ 新增 |
| **python-docx** | Word 提取 | ★ 新增 |
| **Pillow** | 图片预处理 | ★ 新增 |
| **requests** | LLM API 调用 | ★ 新增 |
| **MiKTeX (xelatex)** | 中文 PDF 编译 | ★ 新增（本机安装） |
| pdfplumber（回读） | 验证 PDF 中文渲染 | ★ 新增 |

安装：

```bash
pip install sympy pyyaml pdfplumber python-docx Pillow requests
```

> 所有 Python 依赖装进隔离 venv（`~/.workbuddy/binaries/python/envs/default/`），
> **没有污染系统 Python**。

### CHALLENGE.md 提到但我没采用的参考资源

| 资源 | 状态 |
|---|---|
| `source-text-to-markdown` 技能 | **未采用** —— 未获取该技能，PDF/Word 逻辑自己写的 |
| `doc-pipeline` 技能 | **未采用** —— 同上 |
| `research-md-to-latex` 技能 | **未采用** —— 沿用 starter kit 自带模板 |
| Tesseract OCR | 接口已留，**本机未安装** |
| Qwen / Kimi API | 适配层已写好，**无 key 未实测** |

不假装用了没用过的东西。

---

## 四、Claude 版本 vs 我的版本：逐项差异

| 维度 | Claude 基线版 | 我的版本 | 性质 |
|---|---|---|---|
| 领域数量 | 1（单 YAML 硬加载） | 2（多域注册器） | ★ 架构改进 |
| 领域内容 | 微积分极限 | + 线性代数（13 类题型） | ★ 内容扩展 |
| 输入格式 | Markdown | + PDF / Word / 图片 | ★ 补完空壳 |
| 答案验证 | ❌ 无 | ✅ 7 类恒等式 + PDF 打印 | ★ 新增 Stage |
| LLM 引擎 | Claude（内置） | 可插拔（Qwen/Kimi/DeepSeek/Claude） | ★ 迁移目标 |
| LLM 实测 | ✅ 已验证 | ❌ **未实测（无 key）** | ⚠️ 未达成 |
| 中文 PDF | ⚠️ 脚手架 | ✅ xelatex 8 页中文 PDF | ★ 打通 |
| LaTeX 引擎 | 硬编码 | 自动探测 | 改进 |
| 微积分准确率 | 17/18 = 94.4% | **17/18 = 94.4%** | 持平 |
| 线代准确率 | 不支持 | **13/13 = 100%** | ★ 新增 |
| 正确性验证脚本 | ❌ 无 | ✅ `validate.py`（4 种判定） | ★ 新增 |

### 一句话说清差异

> **架构层**：把"单域硬加载"改成"多域注册"，让加学科从改代码变成填表格。
> **能力层**：补完 PDF/Word/OCR 三个空壳函数，新增线性代数领域。
> **可信层**：新增 Stage 3.5 恒等式验证 —— 让"solved"这个字第一次有了"真的对了"的含义。
> **未达成层**：国产 LLM 只有适配层，没有实测数据。

---

## 五、我的"拿来主义"判断标准

这次我给自己定了三条规矩，写下来供参考：

**① 拿架构，不拿结论。**
starter kit 最有价值的不是那 17/18，而是"知识写在 YAML、代码只做执行"这个设计。
我拿了这个设计，然后用它去装我自己的内容。

**② 拿来的东西要能验证。**
我会先复现基线（17/18），再改；改完再跑回归确认没退化。
**没验证过的"拿来"等于没拿。**

**③ 说清楚哪些是别人的，哪些是自己的。**
17/18 是 Claude 基线的功劳，13/13 是我写的本体的功劳，
而国产模型那一行 —— **是空的，我不填。**

---

## 六、文件清单

| 文件 | 来源 |
|---|---|
| `scripts/pipeline.py` | 拿来 + 改造 |
| `scripts/ingest.py` | 拿来 + 补完 3 个空壳 |
| `scripts/parse_problems.py` | 拿来 + 多域分类 |
| `scripts/classify.py` | 拿来（改其加载方式） |
| `scripts/retrieve.py` | 拿来（未改） |
| `scripts/solve.py` | 拿来 + 新增线代路由与 LLM 兜底 |
| `domain_skills/calculus_limits.yaml` | **拿来（原样）** |
| `scripts/render_latex.py` | 拿来 + 重写 |
| `references/homework_template.tex` | **拿来（原样）** |
| `test_cases/test1_*.md`, `test2_*.md` | **拿来（原样，作回归基线）** |
| — | — |
| `scripts/domain_registry.py` | ★ 自写 |
| `scripts/latex_matrix.py` | ★ 自写 |
| `scripts/linalg_solvers.py` | ★ 自写 |
| `scripts/verify.py` | ★ 自写 |
| `scripts/llm_provider.py` | ★ 自写 |
| `scripts/validate.py` | ★ 自写 |
| `domain_skills/linear_algebra.yaml` | ★ 自写 |
| `test_cases/test3_linear_algebra_zh.md` | ★ 自写 |

原版完整保留在 `c4c-homework-solver-starter/`，可随时跑对照。

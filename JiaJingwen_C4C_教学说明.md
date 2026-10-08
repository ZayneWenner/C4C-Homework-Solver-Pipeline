# JiaJingwen_C4C_教学说明

> 怎么安装、怎么用、支持哪些课程、怎么加一门新课
> 作者：贾静文 Jia Jingwen

---

## 一、三分钟快速开始

### 1. 装依赖

```bash
pip install sympy pyyaml pdfplumber python-docx Pillow requests
```

Python 3.10+ 即可（本机验证环境：Python 3.13.12）。

### 2. 跑第一条命令

```bash
cd JiaJingwen_C4C_homework-solver
python scripts/pipeline.py test_cases/test3_linear_algebra_zh.md output_la/
```

你会看到：

```
✅ 求解完成: 13/13
   经恒等式验证: 11 道
```

输出目录里出现 4 个文件：

| 文件 | 内容 |
|---|---|
| `1_ingested.json` | Stage 1 摄入结果 |
| `2_parsed.json` | Stage 2 题目解析（含分类） |
| `3_solutions.json` | Stage 3 求解 + 验证结果 |
| `homework.tex` | Stage 4 生成的 LaTeX |

### 3. 出 PDF

```bash
python scripts/pipeline.py test_cases/test3_linear_algebra_zh.md output_la/ \
    --compile \
    --course "线性代数 Linear Algebra" \
    --student "贾静文 Jia Jingwen" \
    --title "线性代数作业解答"
```

需要本机有 `xelatex`（中文必需）或 `pdflatex`。程序会**自动探测**，找不到会提示而不崩溃。

- Windows：推荐 [MiKTeX](https://miktex.org/)（首次编译会自动下载中文宏包，稍慢）
- macOS：`brew install --cask mactex`
- Linux：`apt install texlive-xetex texlive-lang-chinese`
- 或者：**不出 PDF 也行**，把 `homework.tex` 传到 [Overleaf](https://www.overleaf.com) 编译

---

## 二、命令行参数

```
python scripts/pipeline.py <输入文件> <输出目录> [选项]

选项：
  --compile            编译 PDF（不加则只出 .tex）
  --course   TEXT      课程名称，默认 "Mathematics"
  --student  TEXT      学生姓名，默认 "Student"
  --title    TEXT      作业标题，默认 "Homework Solutions"
  --lang     zh|en     语言，默认自动判断
  --verbose            详细输出
```

---

## 三、支持的输入格式

| 格式 | 状态 | 依赖 | 备注 |
|---|---|---|---|
| `.md` Markdown | ✅ 完整 | 无 | **推荐**，公式用 `$...$` / `$$...$$` |
| `.pdf` 文本型 | ✅ 可用 | `pdfplumber` | 扫描件不行，见下 |
| `.docx` Word | ✅ 可用 | `python-docx` | 含表格也能读 |
| `.png` / `.jpg` | ⚠️ 需自备 OCR | `pytesseract` | 见下方说明 |
| `.tex` LaTeX | ✅ 可用 | 无 | 正则抽取 |

### 关于图片/扫描件

图片摄入走 Tesseract OCR，**需要你本机先装 Tesseract**：

- Windows：<https://github.com/UB-Mannheim/tesseract/wiki>
- macOS：`brew install tesseract`
- 中文识别还需语言包 `chi_sim`

> ⚠️ 老实说：**OCR 对数学公式的识别效果很差**，拍照作业基本不可用。
> 如果你的作业是手写的，我建议的路线是：用国产模型的 **Vision API**（Qwen-VL / Kimi-Vision）
> 直接读图 —— 那比 OCR 强一个量级。这条路我留了接口但没实测。

---

## 四、支持哪些课程

| 领域 | 覆盖题型 | 状态 |
|---|---|---|
| **微积分极限** | 极限、切线、ε-δ 证明 | ✅ 17/18（继承基线） |
| **线性代数** | 行列式、逆矩阵、秩、转置与迹、线性方程组（唯一/无穷多/无解）、特征值与特征向量、相似对角化、施密特正交化、线性相关性、二次型正定 | ✅ 13/13 |

**未覆盖：** 微分方程、大学物理、概率统计、信号与系统、电路分析。

---

## 五、怎么加一门新课（可复用性）

这是我设计里最想让你能自己做的一件事。加一门课**不需要写 Python**，只需要写一个 YAML。

### 步骤

在 `domain_skills/` 下新建 `你的学科.yaml`，结构如下：

```yaml
domain: probability          # 领域标识
concepts:                    # 概念定义（T-box）
  - id: expectation
    name: 数学期望
    symbols: ["E(X)", "\\mathbb{E}"]
    definition: "离散型 E(X)=Σ xᵢpᵢ；连续型 E(X)=∫xf(x)dx"

classification_rules:        # 分类规则：怎么认出这类题
  - priority: 900            # 数字越大越优先
    type: prob_expectation
    pattern:
      text_contains: ["期望", "数学期望", "expectation"]

solution_methods:            # 解法：认出之后怎么解
  prob_expectation:
    steps: [...]

validation_rules:            # 验证律：解完怎么自检（★ 关键）
  - id: expectation_total
    check: "abs(sum(p) - 1) < 1e-9"
```

然后**什么都不用改** —— `domain_registry.py` 会自动扫描 `domain_skills/*.yaml` 并注册。

### 为什么写成 YAML 而不是 Python

因为**本体是可复用的知识资产，函数是一次性的过程**。

starter kit 原版只能加载**一个** YAML，这是它扩展不了学科的根本原因（不只是没写内容）。
我把它换成多域注册器之后，加学科就从"改架构"降级成了"填表格"。

> 唯一需要写 Python 的情况：你的学科有 SymPy 处理不了的特殊结构
> （比如线代的 `\begin{cases}` 方程组，我为此写了 `latex_matrix.py`）。

---

## 六、接入国产大模型

### 当前状态

**适配层已写好并通过自测，但未接入 key，因此尚未实测。**

无 key 时系统用 `NullProvider`，自动降级到 SymPy + 模板 —— 本次 31 道题全部走的这条路。

### 怎么启用

```bash
cp .env.example .env
```

编辑 `.env`，填一个即可：

```bash
LLM_PROVIDER=qwen        # 可选 qwen / kimi / deepseek / claude

QWEN_API_KEY=sk-xxxxxxxx
# 或 KIMI_API_KEY=...
# 或 DEEPSEEK_API_KEY=...
```

**换模型 = 改这一行，代码零改动。**

### 什么时候 LLM 才会被调用

只有 SymPy 解不出来的题才会走 LLM（证明题、概念题）。
计算题永远走 SymPy —— 因为 SymPy 不会算错，而 LLM 会。

想确认当前用的是哪个引擎：

```bash
python -c "import sys; sys.path.insert(0,'scripts'); \
import llm_provider as L; p=L.get_provider(); \
print(type(p).__name__, p.available, p.model)"
```

---

## 七、怎么验证答案对不对

```bash
python scripts/validate.py output_la/3_solutions.json
```

输出一张逐题对比表。四列的含义：

| 列 | 含义 |
|---|---|
| 标准答案 | 人工写在 `validate.py` 的 `GROUND_TRUTH` 里 |
| 系统输出 | 求解器实际给出的答案 |
| 是否正确 | ✅/❌ |
| 机器自检 | 恒等式验证通过数，如 `2/2 ✔` |

**`—` 表示这道题没有适用的验证律**，不代表错，但也不代表有机器凭据。

### 给你自己加标准答案

编辑 `scripts/validate.py` 的 `GROUND_TRUTH`：

```python
"P14": {
    "kind": "value",            # value / matrix / verdict / general_solution
    "expect": "42",
    "topic": "你的知识点",
    "note": "为什么是这个答案",
},
```

四种 `kind` 的判定强度不同：

| kind | 怎么判 | 强度 |
|---|---|---|
| `value` / `matrix` | `simplify(got − expected)` 逐元素判零 | ★★★ |
| `general_solution` | 取 3 组参数代入原方程验证残差 | ★★★ |
| `verdict` | 关键词包含匹配 | ★☆☆ 弱 |

---

## 八、常见问题

**Q：为什么 PDF 里有些题下面没有"已通过自检"？**
A：那类题没有适用的验证律（如转置、不可逆判定）。不代表答案错，只是缺机器凭据。

**Q：题目没被识别出来怎么办？**
A：检查题号格式。支持 `Problem 1` / `题 1` / `1.` / `1)` / `Q.1`。
最稳的是 Markdown 里用 `## 第 1 题` 或 `### Problem 1`。

**Q：矩阵写进去解析不出来？**
A：用标准 LaTeX 环境：`\begin{bmatrix} ... \end{bmatrix}` 或 `\begin{pmatrix}`。
方程组用 `\begin{cases} ... \end{cases}`。

**Q：能处理手写作业吗？**
A：不能，见第三节。手写请走 Vision API。

**Q：中文 PDF 编译报字体错误？**
A：确认用的是 `xelatex` 而不是 `pdflatex`。程序默认优先 xelatex。

---

## 九、目录结构

```
JiaJingwen_C4C_homework-solver/
├── scripts/
│   ├── pipeline.py          主流水线（串联 Stage 1–5）
│   ├── ingest.py            Stage 1 文档摄入
│   ├── parse_problems.py    Stage 2 题目解析
│   ├── solve.py             Stage 3 求解路由
│   ├── verify.py            ★ Stage 3.5 答案验证
│   ├── render_latex.py      Stage 4 LaTeX 渲染
│   ├── linalg_solvers.py    线性代数求解器
│   ├── latex_matrix.py      LaTeX 矩阵 → SymPy
│   ├── domain_registry.py   ★ 多域本体注册器
│   ├── llm_provider.py      ★ 国产模型适配层
│   └── validate.py          正确性验证
├── domain_skills/
│   ├── calculus_limits.yaml 微积分本体（继承基线）
│   └── linear_algebra.yaml  ★ 线性代数本体（新增）
├── test_cases/              测试用例
└── output_la/               示例输出（含 PDF）
```

★ = 本次新增或重写

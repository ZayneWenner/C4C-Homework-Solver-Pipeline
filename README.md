# JiaJingwen_C4C 交付物总览

> 挑战 C4C：作业自动求解与排版
> 作者：贾静文 Jia Jingwen ｜ 2026-10-08

---

## 一、核心结果（先看这个）

| 指标 | 结果 |
|---|---|
| **线性代数（非极限学科）** | **13/13 = 100%** ✅ |
| **微积分回归（对照 Claude 基线）** | **17/18 = 94.4%**（与基线一致，无退化）✅ |
| 端到端中文 PDF | ✅ 8 页，95 KB |
| 机器恒等式自检通过 | 11 / 13 题 |
| **未达成项** | ⚠️ 国产 LLM 适配层已完成，**但未实测（无 API key）** |

---

## 二、交付文件清单

| 文件 | 说明 |
|---|---|
| `JiaJingwen_C4C_方案设计.md` | 架构、分流策略、国产模型选型、为什么选线代 |
| `JiaJingwen_C4C_验证报告.md` | 13 题逐题对比 + 与 Claude 基线对比 + 判定强度说明 |
| `JiaJingwen_C4C_教学说明.md` | 安装、使用、支持课程、怎么加一门新课 |
| `JiaJingwen_C4C_AI日志.md` | 全过程 AI 使用记录 + AAR 复盘（**评审必需**） |
| `JiaJingwen_C4C_拿来说明.md` | 拿了什么、用了哪些库、与 Claude 版差异 |
| `JiaJingwen_C4C_作业原件.md` | 测试用线性代数作业（13 题，含 2 道陷阱题） |
| `JiaJingwen_C4C_output.pdf` | ★ 生成的中文 PDF（可直接提交） |
| `JiaJingwen_C4C_output.tex` | 对应的 LaTeX 源码 |
| `JiaJingwen_C4C_homework-solver/` | 完整可运行源码（见上级目录） |

---

## 三、三分钟复现所有数字

```bash
cd JiaJingwen_C4C_homework-solver
pip install sympy pyyaml pdfplumber python-docx Pillow requests

# ① 线性代数主测 → 13/13
python scripts/pipeline.py test_cases/test3_linear_algebra_zh.md output_la/
python scripts/validate.py output_la/3_solutions.json

# ② 微积分回归 → 17/18
python scripts/pipeline.py test_cases/test1_tangent_epsilon_delta.md _reg_test1/
python scripts/pipeline.py test_cases/test2_limits.md _reg_test2/

# ③ 生成中文 PDF
python scripts/pipeline.py test_cases/test3_linear_algebra_zh.md output_la/ \
    --compile --course "线性代数 Linear Algebra" \
    --student "贾静文 Jia Jingwen" --title "线性代数作业解答"
```

---

## 四、本次的三个核心改动

**① 多域本体注册器** —— starter kit 只能加载**一个** YAML，这在架构上就堵死了学科扩展。
改成自动扫描注册后，加一门课 = 写一个 YAML，不用碰 Python。

**② Stage 3.5 答案验证** —— 原版 `solved=True` 只表示"跑完没崩"，不表示"答案对"。
对学生来说这是最危险的失败模式：让人在错误答案上感到安全。
现在每道题用领域恒等式自检（A·A⁻¹=I、A·v=λv、Ax−b=0 等），结果打印进 PDF。

**③ 陷阱题** —— 测试集里故意放了 AP12（不可逆矩阵求逆，正确答案就是"不存在"）
和 P6（无穷多解）。**有没有陷阱题，是玩具和工具的分水岭。**
P5 那个隐藏的正则 bug 就是这么被逼出来的。

---

## 五、必须说明的未达成项

**国产大模型的适配层写好了，但没有实测。**

我没有拿到任何国产模型的 API key，因此：

- ✅ `llm_provider.py` 完成并自测通过（Qwen / Kimi / DeepSeek / Claude 统一接口）
- ❌ 一次都没真正调用过
- 本次 31 道题**全部由 SymPy + 模板求解**

我没有把这件事包装成"基于国产模型求解"。
**那个 100% 是 SymPy 的功劳，不是国产模型的功劳。**

原因也很实在：本次线代题型的正确解法本就是符号计算，
强行让 LLM 去算它不擅长的算术，只会把正确率拉低。

需要 LLM 的场景（证明题、概念题）出现时，在 `.env` 填一行 key 即可启用，代码零改动。

---

## 六、已知局限

| 局限 | 说明 |
|---|---|
| LLM 未实测 | 见上 |
| OCR 需自备 Tesseract | 数学公式 OCR 效果差，手写建议走 Vision API |
| 只覆盖 2 个领域 | 微分方程、大学物理、概率统计未做 |
| 弱判定存在 | 13 题中 7 题用"关键词包含"判定，见验证报告第二节 |

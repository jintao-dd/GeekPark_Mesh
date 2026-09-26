# Contributing · GeekPark Mesh

## AI / 检索 / 关系相关改动

涉及 Chunk、FTS、Vector、Rerank、Decision、Gate、Writer、Ask、Agent、并发或 Prompt 的变更：

1. 阅读并遵守 **[AI_EVAL_POLICY.md](./AI_EVAL_POLICY.md)**  
2. 指标定义与 Baseline 记分板见 **[mesh/docs/MESH_BASELINE_v1.md](./mesh/docs/MESH_BASELINE_v1.md)**  
3. PR 描述顶部粘贴 **Eval Delta**（可用 `python mesh/deploy/mesh_baseline.py --compare ... --md-out` 生成）  
4. **Grounding / 安全** 为硬底线（compare exit code 2）

一句话：

> 先测 → 再改 → 再测 → 用 Δ 决定是否保留。禁止「感觉好一点了」。

## 其它

- 不要提交密钥、`.env`、大型 `app-deploy.tgz`、无必要的临时 eval dump（除非有意归档）  
- 前端改动需通过 pre-commit 的 `check_frontend`（`base.html` 中 `app.js` / `style.css` 的 `?v=` 须一致）  
- 详细流水线：`mesh/docs/FULL_PIPELINE_DETAIL.md`  

# CLAUDE.md — 企业微信 RAG 知识库机器人

### Superpowers 工作流
- 所有功能开发使用 superpowers 技能链：brainstorming → writing-plans → TDD → review
- 设计文档存放 `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
- 实现计划存放 `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
- 测试在 `tests/` 目录，pytest 运行（`.venv/Scripts/python -m pytest -q`）
- 全局规格（技术选型、设计决策、核心参数）在 `README.md` 中维护
- 部署手册在 `docs/deploy.md`；**禁止 `docker compose down -v`**

### 测试
- 全部为 mock 测试，不依赖真实外部服务（MinerU/硅基流动/Qdrant）
- Python 3.11 兼容语法（本地 venv 3.14，生产 Docker 3.11）

### Git
- 提交信息用中文
- 不主动 commit/push，等用户明确要求
- 不跳过 pre-commit hooks

### 环境
- 密钥只在 `.env`（gitignored），禁止硬编码
- 联调卡墙时：Docker 镜像用 docker.1ms.run 拉取后 retag；git 推送走用户 Clash 代理

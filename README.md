# 企业微信 RAG 知识库机器人

基于 RAG 的知识库问答机器人：群内 @ 提问 → 向量检索 + 重排 → LLM 生成带来源标注的回答；管理员可热更新知识库（上传/删除/同名替换）。附带纯 Web 聊天页与管理页，无需企业微信也可完整测试检索效果。

> 原始需求：`docs/superpowers/specs/2026-09-14-rag-wechat-bot-spec.md`（企微 RAG 知识库构建规划方案）
> 实施计划：`docs/superpowers/plans/2026-09-14-rag-wechat-bot-plan.md`（T1-T18 分阶段）

## 功能特性

- **RAG 问答**：bge-m3 向量召回（Top 15）→ bge-reranker-v2-m3 精筛（Top 4）→ LLM 生成，回答附来源（文件名 + 块号 + 分数）
- **反幻觉**：无检索命中直接返回固定话术【知识库暂无相关信息】，禁止编造
- **知识库管理**：管理 API（Token 鉴权）+ Web 管理页 + 群指令 `#kb:upload / #kb:delete / #kb:list` 三条路径
- **文档解析**：MinerU 官方 API（pdf/doc/docx，异步任务制）；md/txt 本地解码
- **原子替换**：同名文档先解析新文件，成功才删旧向量再入库；任一步失败旧数据原封不动
- **企微接入**：AES 加解密回调（验签先行）、access_token 缓存刷新、消息分片发送、@ 判定、MsgId 幂等去重、限流、对话上下文（3 轮 / 10 分钟）
- **任务队列**：asyncio.Queue + 3 worker，单任务 30s 超时、失败重试 2 次、积压上限 100
- **Web 测试界面**：聊天页（含检索来源与耗时）+ 管理页（上传/列表/删除）

## 技术选型

| 层级 | 技术 | 理由 |
|------|------|------|
| 后端框架 | FastAPI | 原生 async，回调秒回 200 的场景天然契合 |
| 配置 | pydantic-settings | 类型安全的环境变量管理 |
| 向量库 | Qdrant v1.11.0 + AsyncQdrantClient | payload 过滤（doc_id 删除）、Cosine 检索，容器化轻量 |
| 分块 | langchain-text-splitters | RecursiveCharacterTextSplitter 对中文段落友好 |
| LLM 接入 | 硅基流动 OpenAI 兼容 API | bge-m3 / bge-reranker-v2-m3 / Qwen 全家桶一个 Key 搞定 |
| 文档解析 | MinerU 官方 API | 复杂版面 PDF 还原能力强；md/txt 本地解码零成本 |
| 企微协议 | pycryptodome 自实现 AES-CBC | 官方 80 行协议，无需额外 SDK |
| 部署 | Docker Compose 双容器 | bot + qdrant，qdrant 仅内网暴露 |

---

## 系统架构

```
                        ┌──────────────────────────────────────────────┐
                        │                rag_wechat_bot                │
 企微服务器 ──────────►  │  /wx/callback                                │
                        │    GET  验签回 echostr                        │
                        │    POST 验签→解密→解析→秒回200                 │
                        │         │                                    │
 管理员浏览器 ─────────► │  /static/admin.html ── /api/admin/*           │
 用户浏览器 ───────────► │  / (chat.html) ────── /api/chat               │
                        │         │                                    │
                        │         ▼                                    │
                        │  message_handler（流水线）                     │
                        │         │                                    │
                        │         ▼                                    │
                        │  TaskQueue ── 3 workers                      │
                        │    │            │                            │
                        │    ▼            ▼                            │
                        │  chain.answer   kb_service                   │
                        │    │            │                            │
                        │    ▼            ▼                            │
                        │  retriever    loader/splitter                │
                        │    │            │                            │
                        │    ▼            ▼                            │
                        │  vector_store ──────┐                        │
                        └─────────────────────┼────────────────────────┘
                                              │ 内网
                                        ┌─────▼─────┐
                                        │  qdrant   │ 6333（无宿主机端口）
                                        └───────────┘
```

## 目录结构

```
app/
├── config.py            # pydantic-settings，全部密钥走 .env
├── main.py              # FastAPI 实例、lifespan（建集合/启停队列）、路由挂载、静态托管
├── wx_callback.py       # 企微回调：GET 校验 + POST 接收，_AT_FIELD_CANDIDATES 多源 @ 字段探测
├── wx_crypto.py         # WxCrypto：AES-256-CBC（PKCS7/32）、sha1 验签，官方测试向量钉死
├── wx_token.py          # access_token 单例缓存：锁防并发、提前 10min 刷新、invalidate()
├── wx_sender.py         # message/send、1800 字段落分片、40014 刷新重发、media/get 下载
├── message_parser.py    # is_at_me / extract_question（@ 剔除，邮箱安全）/ MsgId 去重 / #kb 指令 / is_admin
├── message_handler.py   # 流水线：指令路由(先于限流)→限流→空@引导→问题入队
├── context.py           # ContextStore：(chat_id,user_id) 维度 3 轮 10min
├── queue/task_queue.py  # asyncio.Queue 封装：超时/重试/FatalTaskError/on_failure/优雅停机
├── api/
│   ├── admin.py         # 上传/删除/列表，X-Admin-Token (hmac.compare_digest)
│   └── chat.py          # POST /api/chat：同步问答，返回 answer+sources+elapsed_ms
└── rag/
    ├── loader.py        # MinerU 异步适配（上传链接→PUT→轮询→zip 取 md）；md/txt 本地解码
    ├── splitter.py      # 500/80 分块 + 五元元数据
    ├── vector_store.py  # 建集合/批量 upsert(长度守卫)/filter 删除/检索/聚合列表/is_alive
    ├── llm_client.py    # embed/rerank/chat 统一封装，重试 2 次，畸形响应守卫
    ├── retriever.py     # 召回 15 → 阈值过滤 → rerank → Top4（阈值常量在此调）
    ├── chain.py         # Prompt 组装（规范四条约束）、固定话术兜底、answer_with_sources
    └── kb_service.py    # 上传原子替换/回滚、删除、列表，三条管理路径共用
```

## 关键机制

### 企微回调（5s 超时红线）

POST 到达 → `verify_signature`（sha1(sort(token,timestamp,nonce,encrypt))）→ AES-CBC 解密（iv=key[:16]，明文=16B random+4B len+msg+receiveid）→ XML 解析出消息 dict → `message_handler` 投递 → **立即 200 空响应**。任何解析异常也返回 200 + 错误日志，绝不给企微回非 200。

### 消息流水线

```
msg ─► dedup(MsgId, TTL 10min) ─► 指令?#kb:*（管理员→kb_service，180s 超时独立入队）
                                 │（指令路由先于限流：管理员操作不被限流丢弃）
     ─► 限流（用户 5/min，群 2/s，先探测后记账）─► 空@→引导语
     ─► 普通问题入队（30s 超时）─► worker: chain.answer → wx_sender 分片发送
```

文件消息（msg_type=file）进 60s 待传缓存（media_id+file_name），管理员随后发 `#kb:upload` 触发 media/get 下载入库。

### 检索链

```
question ─► embed(bge-m3) ─► search Top15 ─► 阈值过滤(0.2) ─► rerank(Top4, 阈值0.2)
        ─► 空则返回【知识库暂无相关信息】（不调 LLM）
        ─► 拼 Prompt（规范四条约束原文）+ 来源标注（filename 第N块）─► LLM
```

### 知识库原子替换（同名上传）

```
校验(格式/大小) → file_hash 去重 → 读旧文件字节入内存
→ parse 新文件（失败：旧向量无损，抛 ParseError）
→ delete_doc(旧) → chunks → embed → upsert
→ 入库失败：旧字节 re-parse→re-embed→re-upsert 回滚；回滚也失败如实报错
→ 原始文件落 uploads/
```

### 任务队列

`asyncio.Queue(maxsize=100)` + 3 worker。单任务 `wait_for(30s)`（管理员指令 180s）；失败重试 2 次后调 on_failure；FatalTaskError 直接丢弃；stop() 排空时哨兵 QueueFull 回退 await put，保证有界收敛。LLMClientError 语义 →"服务繁忙"；Qdrant/解析类 →"知识库维护中"。

### MinerU 异步适配

```
md/txt → bytes.decode(utf-8→gbk) 直接返回，不碰 MinerU
pdf/docx → POST /api/v4/file-urls/batch → PUT 文件（无鉴权头）
         → 轮询 GET /api/v4/extract-results/batch/{id}（2s 间隔，120s 上限）
         → 下载 full_zip_url → 取最大 .md 内容
```

任何失败抛 ParseError，上层原子替换逻辑保证旧数据无损。

## 安全设计

- 密钥全部 `.env`（gitignored），代码零硬编码
- 管理 API：`X-Admin-Token` 用 `hmac.compare_digest` 比对，fail-closed
- 文件名：入口 `os.path.basename`（含 `\` 归一）防路径穿越
- Qdrant 仅容器内网，无宿主机端口
- Web 聊天接口无鉴权 — **本地测试定位**，公网部署前必须加（见 [docs/deploy.md](docs/deploy.md)）

## 设计决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | 回调模型 | POST 收到即回 200 空响应，全部处理异步化 | 企微 5s 超时红线；RAG 链路秒级耗不起 |
| 2 | 验签顺序 | 先验签再解密 | 企微协议要求；防伪造请求消耗解密资源 |
| 3 | 同名上传顺序 | 先解析新文件 → 删旧向量 → 入库 | 规范两行冲突（"先删后插" vs "解析失败不动旧库"）取折中：解析失败旧数据无损，删除后入库失败有回滚兜底 |
| 4 | @ 判定字段来源 | 多候选字段（atuserlist/AtUserIdList/AtList 等）+ 可配置 WX_BOT_USERID + @all 兜底 | 企微不同版本回调 XML 标签名不统一，多源探测 + 实联校准 |
| 5 | rerank 端点与字段 | POST /v1/rerank；分数接受 `relevance_score` 与 `score` 双兼容 | 实联验证：硅基流动真实返回 `relevance_score` |
| 6 | LLM 模型 | `Qwen/Qwen3-30B-A3B-Instruct-2507` | 规范原文 7B 偏弱；MoE 3B 激活，质量/速度/成本均衡；**模型 ID 必须带组织前缀**（实联验证） |
| 7 | MinerU 接入 | 异步任务制适配层：申请上传链接 → PUT → 轮询（2s 间隔 / 120s 上限）→ zip 取 md | 官方 API 无同步模式；md/txt 不走 MinerU（API 本不支持且无需解析） |
| 8 | 管理指令鉴权 | 群指令路由在限流之前 + 独立 180s 超时入队 | 管理员操作不能被限流丢弃；长耗时不占普通咨询的 30s 超时 |
| 9 | 队列满语义 | 回复"当前咨询量较大，请稍后再试" | 规范硬性要求，不是静默丢弃 |
| 10 | LLM 畸形响应 | _field/_list_field 守卫统一抛 LLMClientError | 走队列重试路径，最终用户看到"服务繁忙"而非"知识库维护中" |
| 11 | 文件名安全 | 入口 os.path.basename（含反斜杠处理） | 群指令/多部分表单的文件名可构造路径穿越 |
| 12 | 错误语义分层 | LLMClientError→"服务繁忙"（可重试）；其余→"知识库维护中" | 用户可区分"稍后再试"和"报告维护" |
| 13 | Web 聊天接口 | 同步返回、无鉴权、无队列、context 固定 ("web","web") | 本地测试定位；多用户隔离留待需要时一行改 |
| 14 | 检索阈值 | 召回 0.2 / rerank 0.2（可调，`app/rag/retriever.py` 常量） | 小知识库（<50 页）场景宁多给 LLM 看，放宽过滤 |
| 15 | Compose version 键 | 省略 | Compose v2 已弃用 `version:` 字段，保留只会告警 |
| 16 | Qdrant 客户端版本 | 服务端钉死 v1.11.0（规范），客户端 1.19 有版本差警告 | 仅警告不影响功能；介意可 `pip install "qdrant-client<1.12"` |

## 核心参数（规范硬性约束）

| 参数 | 值 |
|------|-----|
| 分块 | chunk_size=500, overlap=80, RecursiveCharacterTextSplitter |
| chunk 元数据 | doc_id / filename / file_hash / chunk_index / upload_time（五元强制） |
| 召回 | bge-m3，Top 15 |
| 精筛 | bge-reranker-v2-m3，Top 4 |
| 队列 | asyncio.Queue 上限 100，worker 3，单任务 30s，重试 2 次 |
| 限流 | 单用户 5 次/分钟，单群 2 条/秒 |
| 上下文 | 群ID+用户ID 维度，3 轮，10 分钟过期 |
| 回答分片 | >1800 字符按段落切分，标 (1/2) |
| 文档限制 | pdf/docx/md/txt，≤20MB，file_hash 去重 |
| Qdrant | 1024 维 Cosine，doc_id payload keyword 索引，仅容器内网 |

## 可演进点（预留）

- 队列 Redis 化：TaskQueue 已留 enqueue 协议方法
- 上下文 Redis 化：ContextStore 已留协议
- 来源页码：MinerU 结果带页码后接 page 元数据
- @ 判定校准：`_AT_FIELD_CANDIDATES` 单点扩展

## 交付验收（规范原文要点）

- 功能九项：非@不回 / @+问题正常答 / @无文字引导语 / 无库固定话术 / 上传即生效 / 删除即失效 / 非管理员指令无效 / 长答分片 / 来源标注
- 稳定性三项：10 并发不重不丢（MsgId 幂等）/ 重启数据不丢 / LLM 密钥填错不崩
- 生产：域名回调通过、服务器重启容器自愈、每周备份（**`docker compose down -v` 禁用**）

---

## 快速开始

### 环境要求

- Docker Desktop（推荐），或本机 Python 3.11+ venv + 单独的 Qdrant
- 硅基流动 API Key（LLM/Embedding/Reranker）
- MinerU API Token（仅解析 pdf/docx 需要，[mineru.net](https://mineru.net) 注册获取）

### 1. 配置

```bash
cp .env.example .env   # 然后填写真实值
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `LLM_API_KEY` | 是 | 硅基流动 Key，LLM/Embedding/Reranker 共用 |
| `LLM_MODEL` | 是 | 默认 `Qwen/Qwen3-30B-A3B-Instruct-2507`（注意带组织前缀） |
| `MINERU_API_URL` / `MINERU_API_TOKEN` | 传 pdf/docx 时必填 | `https://mineru.net` + API Token |
| `ADMIN_API_TOKEN` | 是 | 管理页 / 管理 API 鉴权 |
| `QDRANT_URL` | 是 | Docker 内 `http://qdrant:6333`；宿主机直跑用 `http://localhost:6333` |
| `WX_*` 五项 | 企微联调时必填 | CorpID/AgentId/Secret/Token/43位AESKey |

### 2a. Docker 启动（推荐）

```bash
docker compose up -d --build
curl http://localhost:8000/health   # {"status":"ok","queue_size":0,"vector_store":"ok"}
```

### 2b. 宿主机直跑（开发模式）

```bash
# Qdrant 用容器或本机二进制均可，需在 6333 可达，.env 里 QDRANT_URL 填 localhost
pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload
```

> 注意：`.env` 改动不会被 `--reload` 捕获，需重启进程；Docker 模式改后需 `docker compose restart rag_wechat_bot`。

### 3. 使用

- **聊天页**：`http://localhost:8000/` — 提问，答案下方显示命中块、文件名、rerank 分数、耗时
- **管理页**：`http://localhost:8000/static/admin.html` — 输入 `ADMIN_API_TOKEN` 登录，上传/删除文档；同名重传即更新
- **管理 API**：`POST /api/admin/upload`、`DELETE /api/admin/doc/{doc_id}`、`GET /api/admin/docs`，Header `X-Admin-Token`
- **企微**：回调地址 `https://<域名>/wx/callback`，生产部署见 [docs/deploy.md](docs/deploy.md)

## 运行测试

```bash
.venv\Scripts\python -m pytest -q        # 全部 191 项
.venv\Scripts\python -m pytest tests/test_retriever.py -v   # 单模块
```

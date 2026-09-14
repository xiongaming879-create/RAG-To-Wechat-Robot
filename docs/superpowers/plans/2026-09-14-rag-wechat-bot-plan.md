# 企业微信 RAG 知识库机器人 — 分阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 企业微信群 @机器人触发 RAG 知识库问答，管理员热更新知识库，容器化 7×24 部署。

**Architecture:** FastAPI 单服务（回调层 + 管理 API + RAG 核心 + 内存任务队列），Qdrant 独立容器，硅基流动 LLM/Embedding/Reranker 全走外部 API，MinerU 统一文档解析。POST 回调秒回 200 + 异步处理规避企业微信 5s 超时。

**Tech Stack:** Python 3.11 / FastAPI / LangChain / Qdrant / pycryptodome / Docker Compose

**Spec:** `C:\Users\17620\Desktop\rag知识库构建规划方案v1.md`（本计划从 spec 推导，执行时两者都读）

## Global Constraints

- Python 3.11；密钥全部走 `.env`，禁止硬编码
- 企业微信 POST 回调：**先验签再解密**；收到即返回 200 空响应，RAG 全异步
- MsgId 幂等去重，过期 10 分钟
- @ 判定读 `at_users_in_chat` / `at_user_list` 字段，禁止字符串包含匹配
- 非管理员指令忽略/提示无权限；管理员白名单存企业微信 userid（非昵称）
- 分块：chunk_size=500、overlap=80（RecursiveCharacterTextSplitter）
- 检索：bge-m3 召回 TopK=15 阈值 0.60 → bge-reranker-v2-m3 精筛 Top4、丢弃 score<0.5
- chunk 元数据强制：doc_id / filename / file_hash / chunk_index / upload_time
- Qdrant：1024 维、Cosine、doc_id payload 索引、仅容器内网（expose 不 ports）
- 同名文件上传：先删旧向量再插新，失败回滚；file_hash 相同跳过
- LLM/Embedding/Reranker 调用失败重试 2 次、超时 10 秒
- 队列：asyncio.Queue（预留 Redis 接口），worker 3~5，单消息超时 30s，积压上限 100
- 限流：单用户 5 次/分钟，单群 2 条/秒
- 上下文：群ID+用户ID 维度，最近 3 轮，10 分钟过期
- 回答 >1800 字符分片发送；无检索结果固定话术【知识库暂无相关信息】，禁止编造
- Qdrant URL 读 `QDRANT_URL=http://qdrant:6333`；`docker compose down -v` 禁用
- 目录结构、docker-compose.yml、Dockerfile、.env、requirements.txt 按 spec 原文（spec 300-514 行）

## 项目目录（新建）

```
wechat-rag-agent/  (即当前仓库根)
├── app/ (见下方任务)
├── tests/
├── uploads/  qdrant_storage/  logs/
├── .env  .env.example  .gitignore
├── Dockerfile  docker-compose.yml  requirements.txt
```

---

## Task 1: 项目骨架 + 配置 + 健康检查

**依赖:** 无
**Files:**
- Create: `app/config.py`, `app/main.py`, `requirements.txt`, `.env.example`, `.gitignore`, `tests/test_config.py`

**Interfaces:**
- Produces: `app.config.settings`（pydantic-settings 对象，字段与 spec .env 一一对应：`WX_CORP_ID, WX_AGENT_ID, WX_SECRET, WX_TOKEN, WX_AES_KEY, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, EMBEDDING_MODEL, EMBEDDING_DIM=1024, RERANKER_MODEL, MINERU_API_URL, ADMIN_USER_LIST, ADMIN_API_TOKEN, QDRANT_URL, QDRANT_COLLECTION_NAME`）

- [ ] Step 1: 写 `requirements.txt`（spec 原文）、`.gitignore`（.env, uploads/, qdrant_storage/, logs/, __pycache__）、`.env.example`（spec 全部变量）
- [ ] Step 2: 写失败测试 `tests/test_config.py`：settings 能从 env 读出 EMBEDDING_DIM==1024
- [ ] Step 3: 实现 `app/config.py`（pydantic-settings BaseSettings）+ `app/main.py`（FastAPI 实例 + `GET /health` 返回 `{"status": "ok", "queue_size": 0, "vector_store": "unknown"}`）
- [ ] Step 4: `pip install -r requirements.txt && pytest tests/test_config.py -v` → PASS；`uvicorn app.main:app` 后 `curl localhost:8000/health` → 200
- [ ] Step 5: `git add -A && git commit -m "feat: project skeleton, config, health endpoint"`

## Task 2: 企业微信加解密模块 WxCrypto

**依赖:** Task 1
**Files:**
- Create: `app/wx_crypto.py`, `tests/test_wx_crypto.py`

**Interfaces:**
- Produces: `class WxCrypto: verify_signature(token, timestamp, nonce, encrypt_msg) -> bool; decrypt(encrypt_b64) -> str(xml); encrypt(reply_xml) -> str(b64)`；官方企业微信 AES-CBC 逻辑：AESKey=Base64Decode(AESKey+"=")，msg_sha1 签名 = sha1(sort([token,timestamp,nonce,encrypt_msg]))，解密后明文 = 16B random + 4B msg_len + msg + receiveid
- 注：官方提供 Python 示例库（WeChat 官方 sample），用 pycryptodome 自实现 ~80 行

- [ ] Step 1: 写测试：用测试 AESKey 加密一段 XML → decrypt 还原原文；verify_signature 对正确/错误 sha1 分别 True/False
- [ ] Step 2: 运行确认 FAIL（模块不存在）
- [ ] Step 3: 实现 WxCrypto（pycryptodome AES/CBC/NoPadding）
- [ ] Step 4: 测试 PASS
- [ ] Step 5: `git commit -m "feat: wechat AES encrypt/decrypt and signature verification"`

## Task 3: 企业微信回调通路（GET 校验 + POST 接收 + 秒回 200）

**依赖:** Task 1, 2
**Files:**
- Create: `app/wx_callback.py`
- Modify: `app/main.py`（挂路由）
- Test: `tests/test_wx_callback.py`

**Interfaces:**
- Consumes: `WxCrypto`, `settings`
- Produces: 路由 `/wx/callback`：
  - GET：params(msg_signature, timestamp, nonce, echostr) → 验签成功返回解密 echostr 纯文本
  - POST：body XML（encrypt 字段）→ 验签+解密 → 解析出 MsgId/FromUserName/ChatId/MsgType/Content/at 字段 → 交 `message_handler(msg)` 回调 → **立即 Response(200, "")**
  - 解析失败/异常也返回 200 + 错误日志（不崩溃）
- 消息模型（后续任务共用）：`dict` 含 `msgid, msg_type, from_user, chat_id, content, at_userids, is_at_me`

- [ ] Step 1: 测试：GET 验签失败返回非 echostr；POST 加密 XML 进来返回 200 且 message_handler 被调用（用 monkeypatch）
- [ ] Step 2: FAIL → Step 3: 实现（xml.etree 解析、xmltodict 不引入，标准库够） → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: wx callback GET verify + POST receive, async return 200"`

## Task 4: WxAccessToken 缓存刷新

**依赖:** Task 1
**Files:**
- Create: `app/wx_token.py`, `tests/test_wx_token.py`

**Interfaces:**
- Consumes: `settings`（WX_CORP_ID/WX_SECRET）
- Produces: `class WxAccessToken: async get() -> str`（单例，全局锁防并发重复请求；内存缓存；expire_at - 600s 提前刷新；gettoken 失败重试 2 次）

- [ ] Step 1: 测试：mock httpx，第一次 get() 触发请求；token 未过期时第二次 get() 不再请求；过期+10min 内触发刷新
- [ ] Step 2: FAIL → Step 3: 实现（httpx AsyncClient，asyncio.Lock 单例） → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: access_token singleton cache with 10min-early refresh"`

## Task 5: 消息解析（@判定 + 幂等去重 + 指令识别）

**依赖:** Task 3
**Files:**
- Create: `app/message_parser.py`, `tests/test_message_parser.py`

**Interfaces:**
- Consumes: Task 3 消息 dict
- Produces:
  - `is_at_me(msg: dict) -> bool`：读 `at_userids` 列表是否含本应用 userid（spec：从 at_users_in_chat / at_user_list 字段取 userid 列表比对，禁止字符串匹配）
  - `extract_question(msg) -> str`：剔除 @ 标签与首尾空格
  - `dedup(msg) -> bool`：MsgId 内存字典幂等键，命中 False，TTL 10 分钟
  - `parse_command(content) -> Command | None`：前缀 `#kb:upload` / `#kb:delete 文件名` / `#kb:list`，返回 `{"action": ..., "arg": ...}`
  - `is_admin(userid) -> bool`：`userid in settings.ADMIN_USER_LIST`

- [ ] Step 1: 测试：at 列表含/不含各一例；重复 MsgId 第二次 dedup 返回 False；三种指令解析正确；非指令返回 None；admin 判定
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: at-me detection, msgid idempotency, #kb command parsing"`

## Task 6: 异步任务队列（worker/超时/重试/积压）

**依赖:** Task 1
**Files:**
- Create: `app/queue/task_queue.py`, `tests/test_task_queue.py`

**Interfaces:**
- Consumes: 无
- Produces: `class TaskQueue: put(handler, payload) -> PutResult; start(); stop()`；`PutResult = {"ok": bool, "msg": str}`（队列满→ok=False）
- 内部：`asyncio.Queue(maxsize=100)`，`3` 个 worker；每任务 `asyncio.wait_for(timeout=30)`；LLM 类异常重试 2 次（指数退避 1s/2s）；解析类致命错误记日志丢弃；队列抽象留 `enqueue()` 协议方法（Redis 版后续替换）
- Worker 数、超时、上限从 config 读（默认 3 / 30 / 100）

- [ ] Step 1: 测试：put 后 handler 执行；handler 耗时>30s 被 wait_for 打断并回复错误；失败重试 2 次后给失败回调；队列 100 满时 put 返回 ok=False
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: asyncio task queue with workers, timeout, retry, backlog guard"`

## Task 7: MinerU 解析 + 文本分块

**依赖:** Task 1
**Files:**
- Create: `app/rag/loader.py`, `app/rag/splitter.py`, `tests/test_loader.py`, `tests/test_splitter.py`

**Interfaces:**
- Consumes: `settings.MINERU_API_URL`
- Produces:
  - `async parse_file(file_bytes: bytes, filename: str) -> str`：POST MinerU API，返回纯文本；失败 raise `ParseError(reason)`（**不触碰向量库**）
  - `split_text(text: str) -> list[str]`：RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80, separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""])
  - `make_chunks(text, doc_id, filename, file_hash) -> list[dict]`：每块附 doc_id/filename/file_hash/chunk_index/upload_time 五元数据

- [ ] Step 1: 测试：mock MinerU 返回，parse_file 提取文本；MinerU 500 → ParseError；split_text 长文 500 字切块、带 overlap；chunks 元数据完整、chunk_index 递增
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: MinerU parse + 500/80 chunking with mandatory metadata"`

## Task 8: Qdrant 封装（建集合/写入/按 doc_id 删除/检索）

**依赖:** Task 1
**Files:**
- Create: `app/rag/vector_store.py`, `tests/test_vector_store.py`

**Interfaces:**
- Consumes: `settings.QDRANT_URL, settings.QDRANT_COLLECTION_NAME, settings.EMBEDDING_DIM`
- Produces:
  - `ensure_collection()`：1024 维 / Cosine / HNSW + **doc_id payload keyword 索引**（幂等，启动时调用）
  - `async upsert_chunks(chunks: list[dict], vectors: list[list[float]])`：批量 upsert，带五元 payload
  - `async delete_doc(doc_id: str)`：按 doc_id payload 过滤删除
  - `async search(query_vec: list[float], top_k: int = 15) -> list[dict]`：返回 payload + score（Cosine 相似度）
  - `async list_docs() -> list[dict]`：按 doc_id 聚合（filename, **file_hash**, upload_time, chunk 数）——file_hash 供重复上传去重
  - `async is_alive() -> bool`（/health 用）
- 单测用 mock qdrant-client；集成验证放 Task 13

- [ ] Step 1: 测试（mock）：ensure_collection 参数含 1024/Cosine/payload index；delete_doc 用了 doc_id filter；search 返回 payload+score
- [ ] Step 2: FAIL → Step 3: 实现（qdrant-client AsyncQdrantClient） → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: qdrant wrapper, doc_id payload index, batch upsert, filter delete"`

## Task 9: 硅基流动客户端（Embedding + Reranker + LLM）

**依赖:** Task 1
**Files:**
- Create: `app/rag/llm_client.py`, `tests/test_llm_client.py`

**Interfaces:**
- Consumes: `settings.LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, EMBEDDING_MODEL, RERANKER_MODEL`
- Produces（统一 httpx 封装，全部 OpenAI 兼容格式，重试 2 次 / 超时 10s）：
  - `async embed(texts: list[str]) -> list[list[float]]`（批量，单条 ≤8192 token；600 字块安全）
  - `async rerank(query: str, docs: list[str]) -> list[float]`（`/v1/rerank`，单次 ≤20 docs，返回 score 列表）
  - `async chat(system: str, user: str) -> str`（Qwen2.5-7B-Instruct）

- [ ] Step 1: 测试（mock httpx）：embed 批量请求体含 model+input 数组；重试：前 2 次 500 第三次成功 → 返回成功；3 次全失败 → raise；rerank 解析 results score
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: siliconflow embedding/rerank/chat clients with retry"`

## Task 10: 混合检索链路

**依赖:** Task 8, 9
**Files:**
- Create: `app/rag/retriever.py`, `tests/test_retriever.py`

**Interfaces:**
- Consumes: `vector_store.search`, `llm_client.embed/rerank`
- Produces: `async retrieve(question: str) -> list[dict] | None`
  - 流程：embed(question) → 向量召回 TopK=15 → 余弦阈值 0.60 过滤 → rerank(question, 剩余块文本) → score<0.5 丢弃 → 取 Top 4 → 返回 `[{"text", "filename", "page", "score"}]`
  - 召回空 / 全部低于 0.60 / 最高重排分 <0.5 → 返回 None（触发固定话术）

- [ ] Step 1: 测试（mock 两个 client）：15 召回中 3 个低于 0.60 被过滤；重排 <0.5 丢弃；Top4 截断；全低分返回 None
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: hybrid retrieval, wide recall + rerank top4"`

## Task 11: RAG 问答链 + 对话上下文

**依赖:** Task 9, 10
**Files:**
- Create: `app/rag/chain.py`, `app/context.py`, `tests/test_chain.py`, `tests/test_context.py`

**Interfaces:**
- Consumes: `retriever.retrieve`, `llm_client.chat`
- Produces:
  - `async answer(question: str, chat_id: str, user_id: str) -> str`
    - 从 context 拉最近 3 轮拼接历史 → 历史改写优化查询（与原问题一起检索）→ retrieve → None 则返回固定话术 `【知识库暂无相关信息】` → 否则组 Prompt（spec 565-571 行原文）+ 上下文块（带"来源：文件名"标注）→ chat → 答案写入 context
  - `class ContextStore: get(chat_id, user_id) -> list; append(chat_id, user_id, question, answer)`；内存 dict，最近 3 轮，最后消息 10 分钟过期清理，预留 Redis 协议
  - Prompt 硬编码为 spec 原文四条约束

- [ ] Step 1: 测试：检索 None → 固定话术且不调 chat；有结果 → chat 收到的 prompt 含文件名来源；append 后 get 返回该轮；第 4 轮淘汰第 1 轮；过期清空
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: RAG chain with context memory and no-hallucination fallback"`

## Task 12: 消息发送封装（加密 + 分片）

**依赖:** Task 4
**Files:**
- Create: `app/wx_sender.py`, `tests/test_wx_sender.py`

**Interfaces:**
- Consumes: `WxAccessToken.get`, `settings`, （加密回包如开启则复用 WxCrypto）
- Produces: `class WxSender: async send_text(chat_id_or_user, content, at_userid=None)`
  - 调企业微信 `message/send` API（chatid 群发或 userid 应用私聊，回调场景用群发 chatid）
  - >1800 字符按段落分片（不拆句子），标记 (1/2)、(2/2)，顺序逐条发送
  - 失败重试 2 次，token 失效（errcode 40014）刷新 token 重发一次

- [ ] Step 1: 测试（mock）：1800 内单条；2500 字按段落拆 2 条且带 (1/2)(2/2)；中途不拆句子；40014 自动刷新重试
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: wx sender with paragraph chunking and token refresh retry"`

## Task 13: 知识库管理服务（上传/删除/列表 + 原子性）

**依赖:** Task 7, 8, 9
**Files:**
- Create: `app/rag/kb_service.py`, `tests/test_kb_service.py`
- Modify: `app/main.py`（启动时 `ensure_collection()`）

**Interfaces:**
- Consumes: `loader.parse_file`, `splitter.make_chunks`, `llm_client.embed`, `vector_store`
- Produces（三条路径共用，admin API 与群指令都调这层）：
  - 接线 `/health`：启动时 `ensure_collection()`；`/health` 返回真实队列长度（TaskQueue）与向量库连接状态（`is_alive`）
  - `async upload(file_bytes, filename) -> dict`
    - 校验：格式 in {pdf, docx, md, txt}；大小 ≤20MB
    - file_hash=MD5：与库中重复 → `{"status": "skipped"}`
    - 同名已有文档 → **先 parse 新文件（失败则旧向量原封不动）→ 再 delete_doc(旧doc_id) → chunks → embed 批量 → upsert**；任一步失败 → 抛异常；删除成功但入库失败 → 尝试回滚重传旧内容，失败则如实报错（裁定：spec 冲突，见台账）
    - 成功 → 原始文件落 `uploads/` 持久化，返回 `{"status": "ok", "doc_id": ...}`
  - `async delete(doc_id) -> dict`：按 doc_id 删向量
  - `async list_docs() -> list[dict]`

- [ ] Step 1: 测试（mock）：重复 hash 跳过不调 embed；同名先删后插顺序断言；parse 失败时向量库无写入调用；格式/大小拒绝
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: kb service, atomic same-name replace, hash dedup"`

## Task 14: 管理 API（Token 鉴权）+ 群指令接线

**依赖:** Task 5, 12, 13
**Files:**
- Create: `app/api/admin.py`
- Modify: `app/main.py`（挂路由）
- Test: `tests/test_admin_api.py`

**Interfaces:**
- Consumes: `kb_service`, `settings.ADMIN_API_TOKEN`
- Produces:
  - `POST /api/admin/upload`（multipart/form-data，Header `X-Admin-Token` 校验，格式/大小校验）
  - `DELETE /api/admin/doc/{doc_id}`
  - `GET /api/admin/docs`（分页 page/page_size）
  - 401 无效 token；错误带具体原因（如"解析失败: xxx"）
- 群指令接线（在 Task 15 的消息处理里）：管理员发 `#kb:*` → 调 kb_service → 结果 @管理员 回复；非管理员发指令 → 忽略（记日志）

- [ ] Step 1: 测试（TestClient + mock kb_service）：无 token 401；正确 token 上传 200；非法格式 400 带原因；delete/docs 正常
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: admin API with token auth + group command wiring"`

## Task 15: 消息处理流水线组装（端到端问答）

**依赖:** Task 6, 13, 14
**Files:**
- Create: `app/message_handler.py`
- Modify: `app/wx_callback.py`（message_handler 指向此模块）
- Test: `tests/test_message_handler.py`

**Interfaces:**
- Consumes: Task 5 parser、Task 6 队列、Task 11 chain、Task 12 sender、Task 13 kb_service
- Produces: `async handle_message(msg: dict)` — 完整流水线：
  1. 非 @机器人 → 丢弃
  2. 限流：单用户 5 次/分钟、单群 2 条/秒，超限丢弃
  3. 管理员指令 → kb_service → @管理员回执
  4. 空 @（无文字）→ 回"请问有什么可以帮您？"
  5. 普通问题 → 入队 → worker 内：chain.answer → sender 分片发送；LLM 失败重试 2 次后回"服务繁忙，请稍后再试"；Qdrant 失败回"知识库维护中"

- [ ] Step 1: 测试：非@不处理；限流第 6 次丢弃；指令走 kb 分支；空@走引导语；问题走队列且回复调 sender
- [ ] Step 2: FAIL → Step 3: 实现 → Step 4: PASS
- [ ] Step 5: `git commit -m "feat: full message pipeline, rate limit, command/question routing"`

## Task 16: Docker 化

**依赖:** Task 1~15 全部
**Files:**
- Create: `Dockerfile`, `docker-compose.yml`（均按 spec 410-496 行原文，一字不改）

- [ ] Step 1: 写 Dockerfile（python:3.11-slim、TZ=Asia/Shanghai、uvicorn 启动）+ docker-compose.yml（rag_wechat_bot 8000 映射 + qdrant v1.11.0 仅 expose 6333、rag_internal_net、资源限制 1C/1G、日志轮转 10m×3、restart always、卷挂载 uploads/qdrant_storage/logs）
- [ ] Step 2: `docker compose up -d --build` 两容器 Up
- [ ] Step 3: `curl localhost:8000/health` → 200 且 vector_store=ok；`docker compose ps` 确认 qdrant 无宿主机端口映射
- [ ] Step 4: `docker compose restart rag_wechat_bot` → 自动恢复；`docker compose down`（**无 -v**）再 up → 数据仍在
- [ ] Step 5: `git commit -m "chore: dockerize, qdrant internal only"`

## Task 17: ngrok 联调 + 本地闭环验收

**依赖:** Task 16
**Files:** 无新代码（验证 + 修复）

- [ ] Step 1: 填 .env 真实测试密钥；`ngrok http 8000`；企业微信后台回调地址填 `https://xxx.ngrok.io/wx/callback`，保存校验通过
- [ ] Step 2: 按 spec「交付验收标准」功能项逐条群内实测：非@不回 / @+问题正常答 / @无文字引导语 / 无库固定话术 / 上传即生效 / 删除即失效 / 非管理员指令无效 / 长答分片
- [ ] Step 3: 稳定性项：10 次并发提问不重复不丢（验证 MsgId 幂等）；`docker compose down && up` 数据不丢；LLM 密钥故意填错 → 服务不崩，回错误提示
- [ ] Step 4: 发现问题修复后回归测试，`git commit`

## Task 18: 腾讯云生产部署（人工操作，Claude 提供步骤与脚本）

**依赖:** Task 17
**Files:**
- Create: `docs/deploy.md`（部署手册）

- [ ] Step 1: 服务器 git clone + 生产 .env；`docker compose up -d --build`
- [ ] Step 2: Nginx 反代 8000（client_max_body_size 20M、60s 超时、HTTP→HTTPS、真实 IP 透传）+ 域名证书
- [ ] Step 3: 企业微信后台回调切正式域名，验证；重启服务器验证容器自动拉起
- [ ] Step 4: 每周备份脚本（qdrant_storage + uploads），**down -v 红线写在手册首行**

---

## Verification（总验收）

1. **单元测试**：`pytest tests/ -v` 全绿（Task 1~15 各自带测试）
2. **本地容器**：`docker compose up -d --build` 后 /health 200、qdrant 无公网端口、重启数据不丢
3. **功能验收**：Task 17 Step 2 九项逐条勾
4. **稳定性验收**：Task 17 Step 3 并发/重启/故障注入三项
5. **生产**：Task 18 域名回调通过、服务器重启自动恢复

## 执行顺序依赖图

```
T1 ┬ T2 ─ T3 ─ T5 ─────┐
   ├ T4 ────── T12 ────┤
   ├ T6 ───────────────┤
   ├ T7 ─ T13 ─ T14 ───┼─ T15 ─ T16 ─ T17 ─ T18
   ├ T8 ─┬ T10 ┬       │
   └ T9 ─┴     └ T11 ──┘
```
T5/T12/T14 彼此独立可并行；T15 前全部就绪。

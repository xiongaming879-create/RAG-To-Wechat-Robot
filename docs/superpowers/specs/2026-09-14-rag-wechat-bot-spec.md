# 企业微信RAG知识库机器人开发方案
> 项目：企业微信群@触发RAG问答Agent
> 开发模式：本地Windows+Docker Desktop + ngrok联调；生产：腾讯云轻量服务器 docker‑compose 7×24部署
> 开发工具：Claude Code
> 核心特性：动态热更新知识库，无需重启服务；仅@机器人响应；异步处理规避企业微信5s回调超时；RAG防幻觉；管理员权限管控

## 目录
- [项目整体说明](#项目整体说明)
- [技术栈](#技术栈)
- [整体架构](#整体架构)
- [企业微信对接层细化（最高优先级）](#企业微信对接层细化最高优先级坑最多必须细化)
- [异步任务队列细化](#异步任务队列细化)
- [RAG核心层细化](#rag核心层细化)
- [管理员与权限体系](#管理员与权限体系)
- [对话上下文管理](#对话上下文管理)
- [稳定性与异常处理](#稳定性与异常处理)
- [硬性业务规则](#硬性业务规则)
- [项目目录结构](#项目目录结构)
- [环境变量 .env](#环境变量-env)
- [开发顺序](#开发顺序)
- [Docker相关配置文件](#docker相关配置文件)
- [requirements.txt](#requirementstxt)
- [本地开发调试流程](#本地开发调试流程)
- [腾讯云生产部署流程](#腾讯云生产部署流程)
- [Prompt约束](#prompt约束)
- [避坑清单](#避坑清单)
- [交付验收标准](#交付验收标准)

## 项目整体说明
开发一套企业微信群机器人，群内@机器人触发知识库问答。
1. 本地电脑完成全部业务开发，使用ngrok做内网穿透对接企业微信回调联调
2. 上线腾讯云轻量服务器，容器化部署，7×24小时自动恢复运行
3. 支持管理员上传/删除文档，知识库实时同步生效，**不需要重启服务**
4. 支持文档格式：PDF / MD / TXT / DOCX
5. 知识库检索不到信息，禁止编造内容，返回固定提示文本
6. 只有@机器人才回复消息，普通群消息忽略不处理

## 技术栈
### 后端主体
- Python 3.11
- FastAPI：主web服务、回调接口、管理员接口、健康检查接口
- LangChain：RAG业务流程
- Qdrant：向量数据库，容器部署

### 文档解析
- **统一走 MinerU API 服务解析文档**（PDF / DOCX / MD / TXT 全部支持）
- API 地址后续开发到该环节时提供，代码预留配置项
- 不再使用 PyMuPDF / python-docx 本地解析

### Embedding
- 调用硅基流动 Embedding API，模型：BAAI/bge-m3（1024 维）
- OpenAI 兼容格式 /v1/embeddings，支持批量输入
- 混合检索：BAAI/bge-m3 向量召回 + BAAI/bge-reranker-v2-m3 重排
- API 地址与密钥后续开发到该环节时提供，先全部写入 .env 预留配置

### 通讯
- 企业微信自建应用回调API
- ngrok：本地HTTPS内网穿透联调
- 内存异步任务队列：解决企业微信5秒回调超时问题

### 部署
- Dockerfile + docker‑compose.yml
- 全部服务容器化，数据宿主机卷挂载持久化
- restart: always 异常自动重启

## 整体架构
### 1.企业微信消息层
- 接收企业微信回调GET校验、POST消息推送
- 判断是否@机器人；非@消息直接丢弃
- POST请求立刻返回200空响应，消息丢入异步队列，**RAG问答全部异步执行**

### 2.RAG核心服务层
- 文档上传 → 解析 → 文本分块 → 向量化 → Qdrant入库
- 文档删除 → 按doc_id删除该文档全部向量数据
- 用户问题向量检索、上下文组装、调用LLM生成回答

### 3.管理员API层
- 文档上传接口
- 删除文档接口
- 获取已上传文档列表接口
- 通过企业微信userid白名单做权限校验
- 管理API增加简单Token鉴权，不能公网裸奔

### 4.健康检查层
- GET /health 接口：返回服务状态、队列长度、向量库连接状态
- 用于后续监控告警

## 企业微信对接层细化（最高优先级，坑最多，必须细化）
原规划只说了"接收回调、判断@机器人"，实际开发 80% 的坑都在这里，必须补全以下细节。

### 1. 消息加解密（AES）完整逻辑
**为什么要细化**：企业微信回调默认支持AES加密模式，明文模式可以用但官方不推荐；如果配置了 EncodingAESKey，收到的 POST 消息体是加密的，不解密全是乱码，90% 新手第一次都会卡在这里。

细化要求：
- GET 校验接口兼容明文模式
- POST 接口必须实现 AES 解密 + 签名校验两步，**先验签再解密**
- 解密后得到真实的消息 XML/JSON 结构体
- 发送消息时也需要对应加密（如果开启加密）

### 2. AccessToken 获取与缓存机制
**为什么要细化**：调用企业微信"发消息回群"API 必须携带 access_token，有效期 2 小时；每次发消息都重新获取会触发接口限流，直接报错。

细化要求：
- 服务启动时获取一次 access_token，存入内存缓存
- 提前 10 分钟自动刷新，避免过期
- 获取失败重试机制
- 全局单例获取，禁止并发重复请求
- 代码封装成 WxAccessToken 类，业务层直接拿 token

### 3. @机器人的精准判定逻辑
**为什么要细化**：不能用简单的"字符串是否包含@机器人名称"来判断——昵称重复、用户手动输入@文字、多个艾特都会误判。

细化要求：
- 从消息体的 at_users_in_chat / at_user_list 字段中读取被@的 userid 列表
- 判断列表中是否包含本应用的 agentid 对应的 userid
- 提取问题文本时，自动剔除@标签、首尾空格
- 只@机器人但没文字的情况，返回引导话术："请问有什么可以帮您？"

### 4. 消息幂等去重（防重复推送）
**为什么要细化**：企业微信回调 5 秒内没收到 200，会重复推送同一条消息最多 3 次；如果 RAG 耗时稍长，即使异步处理，也可能出现重复回答。

细化要求：
- 用消息体的 MsgId 做幂等键
- 收到消息先查缓存，已经处理过的直接返回 200，不重复入队
- 幂等键过期时间 10 分钟

### 5. 管理员群内指令协议细化
**为什么要细化**：原规划只提了"#上传 / #删除"，没有明确格式，代码实现会很随意。

细化要求：
- 指令前缀：`#kb:upload`、`#kb:delete 文件名`、`#kb:list`
- 管理员发送文件 + 附带指令文本，自动识别并入库
- 指令执行结果主动@管理员回复（成功/失败原因）
- 非管理员发送指令，直接忽略或提示无权限

### 6. 消息回复长度限制与分片
**为什么要细化**：企业微信单条文本消息有长度上限（约 2048 字符），超长会被截断，知识库长回答直接废了。

细化要求：
- 回答超过 1800 字符自动分片，按段落拆分
- 分片按顺序逐条发送，标记 1/2、2/2
- 避免中间拆开一句话

## 异步任务队列细化
原规划只说"消息丢队列"，但队列怎么实现、异常怎么处理完全没说，直接决定会不会消息堆积、丢失、重复。

### 队列选型
- 基础版：Python asyncio.Queue 内存队列（单容器足够，无需额外 Redis，降低复杂度）
- 生产增强版：Redis List 做队列（支持多实例、持久化）
- 先实现内存队列，预留 Redis 替换接口

### 消费逻辑
- 固定并发数（默认 3~5 个 worker），避免并发太高打爆 LLM API
- 单条消息处理超时设置（默认 30 秒），超时自动终止并回复"服务繁忙，请稍后再试"

### 失败重试
- LLM 调用失败自动重试 2 次
- 重试失败返回友好错误提示
- 致命错误（解析失败）丢弃并记录日志，不无限重试

### 积压保护
- 队列长度上限 100 条，超过直接返回"当前咨询量较大，请稍后再试"
- 避免服务被打挂

## RAG核心层细化
原规划偏流程化，具体参数和实现标准缺失，会导致检索效果差、知识库删不干净、更新有残留。

### 1. 文档解析：统一走 MinerU
- 所有格式（PDF / DOCX / MD / TXT）统一调用 MinerU API 服务解析，不再本地解析
- API 地址后续提供，配置项：MINERU_API_URL
- 解析失败不修改原有向量库，返回具体失败原因给管理员

### 2. Embedding：硅基流动 bge-m3
- 接口：/v1/embeddings，OpenAI 格式兼容
- 模型名：BAAI/bge-m3，输出 1024 维
- 单条输入不超过 8192 token（600 字符分块完全安全）
- 支持批量输入，更新时批量生成向量，减少请求次数
- 调用失败自动重试 2 次，超时 10 秒

### 3. 文本分块策略具体参数
- 分块器：LangChain RecursiveCharacterTextSplitter
- chunk_size = 500 字符
- chunk_overlap = 80 字符
- 按语义优先分割（标题、段落、句号）

### 4. 块元数据强制规范
每个 chunk 入库时必须携带以下元数据，缺一不可：

| 字段 | 作用 |
|------|------|
| doc_id | 文档唯一 ID（UUID），更新/删除时按此字段批量删除所有关联块 |
| filename | 原始文件名，管理员可识别 |
| file_hash | 文件 MD5，相同文件重复上传直接跳过 |
| chunk_index | 块序号，保证拼接顺序 |
| upload_time | 上传时间戳 |

### 5. 检索链路：向量宽召回 + 重排精筛（混合检索）
分块策略必须和检索链路配套才能发挥效果，完整流程：

**第一步：向量召回（bge-m3）**
- 检索 TopK = 15 个块（比无重排时多一倍，宽召回）
- 相似度阈值：0.60（余弦相似度，低于阈值直接丢弃）
- 目的：尽可能把所有可能相关的块都捞出来，交给重排筛选

**第二步：重排精筛（bge-reranker-v2-m3）**
- 输入：用户问题 + 召回的 15 个块文本
- 输出：按相关性得分从高到低排序
- 截断：取 Top 4 作为最终上下文，得分低于 0.5 的块直接丢弃

**第三步：上下文组装**
- 按重排后的顺序拼接块内容
- 保留来源标注（文件名 + 页码）
- 送入 LLM 生成最终回答

效果：既保证召回率，又通过重排过滤向量检索噪音，最终上下文质量远高于纯向量检索。

最高得分低于阈值，直接触发"知识库暂无相关信息"。

### 6. 重排接口注意事项（bge-reranker-v2-m3）
- 模型名：BAAI/bge-reranker-v2-m3
- 单次最多传入 20 个候选文档，召回 15 个刚好
- 返回 score 字段，值越高相关性越强

### 7. 硅基流动 API 密钥配置
- 全部写入 .env 文件，禁止硬编码
- 调用失败自动重试 2 次，超时 10 秒

### 8. Qdrant 侧配套配置
- 集合维度：1024（严格对应 bge-m3 输出维度）
- 距离度量：Cosine（余弦相似度）
- 索引：默认 HNSW 即可，文档量不大不需要优化
- **必须开启 payload 索引，针对 doc_id 字段建索引**：频繁更新场景下 doc_id 过滤删除是高频操作，建索引后删除速度差 10 倍以上

### 9. 文档更新/删除的原子性
- 上传前先算文件 MD5（file_hash），相同文件重复上传直接跳过
- 上传同名文件：先删除旧文档所有向量 → 再解析插入新文档 → 全程失败回滚
- 禁止"先插后删"，避免中间状态出现新旧内容混杂
- 解析失败不修改原有向量库

## 管理员与权限体系
原规划只有"管理员白名单"，具体校验逻辑、接口边界都不明确。

### 管理员身份校验
- 白名单存储企业微信 userid，不是昵称
- 所有管理操作（上传、删除、列表）都必须校验发送者 userid 是否在白名单
- 管理 API 接口增加简单 Token 鉴权，不能公网裸奔

### 文档管理接口规范
- POST /api/admin/upload：上传文件，支持 multipart/form-data
- DELETE /api/admin/doc/{doc_id}：删除指定文档
- GET /api/admin/docs：获取文档列表，分页
- 最大单文件：20MB
- 支持格式：pdf, docx, md, txt；其他格式直接拒绝

### 双管理路径
- 路径 1：网页后台 API（正式生产用）
- 路径 2：群内管理员指令（快速更新用）
- 两条路径共用底层 RAG 服务

## 对话上下文管理
原规划只提了"短期记忆"，怎么存、存多久、怎么用完全没说。

- 存储维度：按 **群ID + 用户ID** 维度独立存储上下文，不串对话
- 存储内容：只存最近 3 轮问答（用户问题 + 助手回答）
- 过期时间：最后一条消息后 10 分钟自动清空
- 拼接规则：检索前先带上历史上下文优化查询，回答后追加进历史
- 存储位置：内存字典（基础版），预留 Redis 接口

## 稳定性与异常处理（7×24 必备）
### 1. 全链路异常兜底
- 企业微信回调异常：立刻返回 200，记录日志，不崩溃
- LLM 调用失败：重试 2 次，失败返回友好提示
- 向量库连接失败：返回"知识库维护中"
- 文档解析失败：管理员上传时返回具体失败原因

### 2. 限流与防刷
- 单用户每分钟最多 5 次提问
- 单群每秒最多 2 条消息
- 超过直接丢弃，不回复

### 3. 健康检查接口
- 新增 GET /health 接口，返回服务状态、队列长度、向量库连接状态

### 4. 日志规范
- 分级：INFO / WARN / ERROR
- 关键日志：收到消息、调用 RAG、回答耗时、错误栈
- 所有异常必须打日志，不能静默失败

## 硬性业务规则
1. 非@机器人消息直接丢弃，不做任何处理，不回复
2. 知识库优先，检索无有效内容，回复固定话术，禁止编造外部知识
3. 文档上传删除实时生效，热更新，**不重启服务**
4. 企业微信POST回调接口禁止同步执行RAG逻辑，必须异步任务处理，快速返回响应
5. 知识库管理接口仅允许配置内管理员userid操作
6. 支持简单同群同用户短期对话上下文记忆
7. Qdrant向量库禁止暴露公网端口，仅容器内部网络访问
8. POST回调先验签再解密；用MsgId做幂等去重，防止重复回答
9. 管理API必须Token鉴权，禁止公网裸奔

## 项目目录结构
```plaintext
wechat‑rag‑agent/
├── app/
│   ├── main.py            # FastAPI 入口（含 /health）
│   ├── wx_callback.py     # 企业微信回调校验、AES解密、消息接收解析
│   ├── wx_token.py        # WxAccessToken 类：access_token 获取、缓存、自动刷新
│   ├── wx_sender.py       # 企业微信发送消息封装（含加密、分片发送）
│   ├── rag/
│   │   ├── loader.py      # MinerU API 文档解析调用
│   │   ├── splitter.py    # 文本分块（RecursiveCharacterTextSplitter，500/80）
│   │   ├── vector_store.py # Qdrant 增删查封装（1024维/Cosine/doc_id payload索引）
│   │   ├── retriever.py   # 混合检索：bge-m3 宽召回 + reranker 精筛
│   │   └── chain.py       # RAG 问答链路（上下文组装、来源标注）
│   ├── queue/
│   │   └── task_queue.py  # 异步任务队列（worker、超时、重试、积压保护）
│   ├── api/
│   │   ├── admin.py       # 管理员知识库接口（Token 鉴权）
│   │   └── query.py       # 问答接口
│   ├── context.py         # 对话上下文管理（群ID+用户ID 维度）
│   ├── config.py          # 读取.env 全局配置
├── uploads/               # 用户上传原始文档持久化
├── qdrant_data/
├── qdrant_storage/        # Qdrant 向量库数据
├── logs/                  # 日志目录
├── .env                   # 环境密钥配置（不提交 git）
├── Dockerfile
├── docker‑compose.yml
└── requirements.txt
```

## 环境变量 .env
> ⚠️ .env文件严禁提交版本管理

```env
# ========== 企业微信自建应用配置 ==========
WX_CORP_ID="wwxxxxxx"
WX_AGENT_ID=1000001
WX_SECRET="xxxxxxxxxxxxxxxxxxxx"
WX_TOKEN="xxxxxxxx"
WX_AES_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# ========== LLM大模型配置（硅基流动） ==========
LLM_API_KEY="sk-xxx"
LLM_BASE_URL="https://api.siliconflow.cn/v1"
LLM_MODEL="Qwen2.5-7B-Instruct"

# ========== Embedding / Reranker（硅基流动，与LLM共用密钥） ==========
EMBEDDING_MODEL="BAAI/bge-m3"
EMBEDDING_DIM=1024
RERANKER_MODEL="BAAI/bge-reranker-v2-m3"

# ========== MinerU文档解析API ==========
MINERU_API_URL="http://xxx"

# ========== 管理员白名单，企业微信userid数组 ==========
ADMIN_USER_LIST=["user1","user2"]

# ========== 管理API Token鉴权 ==========
ADMIN_API_TOKEN="xxxxxxxx"

# ========== Qdrant容器内部地址，本地/生产统一无需修改 ==========
QDRANT_URL="http://qdrant:6333"
QDRANT_COLLECTION_NAME="wechat_rag_kb"
```

## 开发顺序
严格按顺序开发，回调不通后续全部无法测试。

### 阶段 1：企业微信回调基础通路
- 实现 /wx/callback GET 接口：企业微信后台保存配置校验
- 实现 /wx/callback POST 接口接收消息；**先验签再AES解密**；收到消息立刻返回空 200，消息丢异步队列
- ngrok 本地联调，确认企业微信可以成功推送消息到本地服务

### 阶段 2：消息解析逻辑
- 判断文本消息；从 at_users_in_chat / at_user_list 字段判断是否 @机器人
- 提取用户提问（剔除@标签）；区分管理员指令与普通问答
- MsgId 幂等去重

### 阶段 3：RAG 基础能力
- MinerU API 接入，统一解析 PDF / DOCX / MD / TXT
- 文本分块（RecursiveCharacterTextSplitter，chunk_size=500, overlap=80）
- Qdrant 建集合（1024 维、Cosine、doc_id payload 索引），向量批量新增、按 doc_id 删除能力
- chunk 元数据强制规范：doc_id / filename / file_hash / chunk_index / upload_time
- 混合检索链路：bge-m3 宽召回 TopK=15、阈值 0.60 → bge-reranker-v2-m3 重排取 Top 4、丢弃 score<0.5 → 上下文组装（带文件名+页码来源标注）

### 阶段 4：管理员知识库热更新
- 上传文档接口，MinerU 解析入库实时生效；file_hash 相同直接跳过；同名文件先删旧向量再入库，失败回滚
- 删除文档接口，按 doc_id 清理对应向量
- 获取文档列表接口；白名单权限校验 + Token 鉴权

### 阶段 5：问答业务逻辑
- @机器人触发 RAG 问答
- 知识库无信息返回固定提示；支持短期上下文（群ID+用户ID，最近3轮，10分钟过期）
- 回答超 1800 字符自动分片发送

### 阶段 6：Docker 化适配
- Dockerfile、docker‑compose.yml；卷挂载持久化数据；服务自动重启；qdrant 不暴露公网端口
- 资源限制、日志轮转、时区 Asia/Shanghai
- 本地 docker compose up -d --build 完整跑通整套服务

### 阶段 7：本地闭环完整测试
- ngrok 联调企业微信；上传文档提问；删除文档验证知识失效；非 @消息不响应；并发无超时重复推送
- 按[交付验收标准](#交付验收标准)逐条验收

### 阶段 8：腾讯云生产部署
- 代码上传服务器；服务器启动容器；配置 Nginx+HTTPS 域名；企业微信切换正式回调地址

## Docker相关配置文件

### docker‑compose.yml

```yaml
version: '3.8'

services:
  rag_wechat_bot:
    build: .
    container_name: rag_wechat_bot
    restart: always
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./uploads:/app/uploads
      - ./qdrant_data:/app/qdrant_data
      - ./logs:/app/logs
    depends_on:
      - qdrant
    networks:
      - rag_internal_net
    # 资源限制，防止单个服务吃光服务器
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1G
    # 日志轮转，防止日志占满磁盘
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  qdrant:
    image: qdrant/qdrant:v1.11.0
    container_name: rag_qdrant
    restart: always
    volumes:
      - ./qdrant_storage:/qdrant/storage
    expose:
      - "6333"
    networks:
      - rag_internal_net
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

networks:
  rag_internal_net:
    driver: bridge
```

Qdrant 访问地址代码读取环境变量 QDRANT_URL=http://qdrant:6333，容器内网访问，不映射宿主机端口。

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 时区设置，避免日志时间不对
ENV TZ=Asia/Shanghai
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app /app/app

RUN mkdir -p /app/uploads /app/logs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> 说明：多阶段构建可在镜像体积成为问题时引入；当前单阶段足够，本地开发可用卷挂载源码热调试，不必把源码反复打进镜像。

### requirements.txt

```txt
fastapi>=0.112.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
langchain>=0.3.0
langchain-community>=0.3.0
langchain-openai>=0.2.0
langchain-qdrant>=0.2.0
qdrant-client>=1.11.0
python-dotenv>=1.0.0
pycryptodome>=3.20.0
pydantic>=2.8.0
```

> pycryptodome 用于企业微信 AES 加解密与签名校验。

## 本地开发调试流程
1. 启动 Docker Desktop
2. 项目根目录放置 .env 填入本地测试密钥
3. 构建启动容器

```bash
docker compose up -d --build
# 查看日志
docker compose logs -f rag_wechat_bot
```

4. ngrok 暴露本地 8000 端口

```bash
ngrok http 8000
```

5. 企业微信自建应用回调地址填写：https://xxx.ngrok.io/wx/callback
6. 在企业微信群测试 @机器人问答、知识库上传删除功能

## 腾讯云生产部署流程
1. 将完整项目上传腾讯云轻量服务器（git clone / scp）
2. 服务器根目录放置生产环境 .env 配置文件
3. 服务器执行

```bash
docker compose up -d --build
```

4. Nginx 反向代理服务 8000 端口，配置域名 HTTPS 证书
5. 企业微信后台修改回调地址为正式域名：https://your-domain.allroads-ai.cn/wx/callback
6. 验证全流程，服务器重启验证服务自动拉起

### Nginx 反向代理要点
- 上传文件大小限制：client_max_body_size 20M
- 代理超时时间：60 秒
- HTTP 强制跳转 HTTPS
- 真实 IP 透传（X-Real-IP / X-Forwarded-For）

### 数据持久化与备份
- 每周备份一次 qdrant_storage 和 uploads 目录
- 容器删除、重建、升级，数据不丢失需验证
- **⚠️ 红色警告：禁止使用 `docker compose down -v`，会删除卷数据**

## Prompt约束
代码内硬编码 Prompt

```plaintext
你是内部知识库问答助手，只能根据给定的知识库片段回答用户问题。
1. 严格仅使用提供的知识库内容作答
2. 如果知识库内容不足以回答，直接回复：【知识库暂无相关信息】
3. 禁止编造、拓展、联想外部知识
4. 回答简洁、准确、贴合原文
```

## 避坑清单
- ❗企业微信 POST 回调接口禁止同步运行 RAG 逻辑，必须异步任务，快速返回响应，避免 5s 超时导致企业微信重复推送消息
- ❗配置了 EncodingAESKey 后 POST 消息体是加密的，必须先验签再解密，否则全是乱码
- ❗禁止使用 docker compose down -v，会删除 qdrant_storage 卷数据
- access_token 有效期 2 小时，必须缓存 + 提前 10 分钟刷新，禁止每次发消息重新获取（触发限流）
- @机器人判定必须读消息体 at 字段列表，禁止字符串包含匹配（昵称重复、手动输入会误判）
- Qdrant 只允许容器网络访问，禁止映射 6333 宿主机公网端口
- 所有密钥全部读取 .env 环境变量，禁止硬编码代码
- 知识库更新直接操作向量库，不做服务启动预加载文档
- 必须校验消息是否 @机器人，否则直接丢弃消息
- uploads、qdrant_storage 目录宿主机挂载，删除容器数据不会丢失
- Embedding 维度（1024）与 Qdrant collection 维度、Cosine 距离度量必须一致，换模型需重建 collection
- Qdrant 必须对 doc_id 字段建 payload 索引，否则按文档删除向量慢 10 倍以上
- bge-m3 单条输入不超 8192 token；reranker 单次最多 20 个候选文档；硅基流动调用失败重试 2 次、超时 10 秒
- 上传前先算 file_hash（MD5），相同文件重复上传直接跳过
- 上传同名文件必须"先删旧向量再插新向量"，禁止先插后删造成新旧混杂

## 交付验收标准
### 功能测试
- ✅ 企业微信后台保存回调地址，校验通过
- ✅ 群内普通消息，机器人不回复
- ✅ @机器人 + 问题，正常返回答案
- ✅ @机器人但没文字，返回引导语
- ✅ 知识库没有的问题，返回固定提示，不瞎编
- ✅ 管理员上传文档，立刻提问能查到新内容
- ✅ 管理员删除文档，立刻提问查不到旧内容
- ✅ 非管理员发管理指令，无效果
- ✅ 长回答自动分片发送

### 稳定性测试
- ✅ 连续 10 次并发提问，不重复、不丢失、不超时
- ✅ 关闭 Docker 再启动，向量库数据不丢失
- ✅ 重启服务器，所有容器自动拉起，服务恢复
- ✅ LLM API 故障，服务不崩溃，返回错误提示
- ✅ 本地 docker compose 完整启动整套服务
- ✅ ngrok 内网穿透完成企业微信回调联调
- ✅ 异步任务处理消息，无回调超时问题
- ✅ 容器崩溃 / 服务器重启全部服务自动恢复运行
- ✅ 腾讯云部署完成 7×24 小时运行

## 投喂 Claude Code 附加指令

```plaintext
严格遵循本md文档全部内容完成项目开发，目录结构、docker配置、环境变量、业务规则全部遵守。
Qdrant访问地址读取环境变量QDRANT_URL。密钥禁止硬编码。开发完成输出完整项目文件树，输出本地测试操作步骤。
```

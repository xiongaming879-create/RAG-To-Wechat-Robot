from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 企业微信自建应用
    WX_CORP_ID: str = ""
    WX_AGENT_ID: int = 0
    WX_SECRET: str = ""
    WX_TOKEN: str = ""
    WX_AES_KEY: str = ""

    # LLM（硅基流动）
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = ""

    # Embedding / Reranker
    EMBEDDING_MODEL: str = ""
    EMBEDDING_DIM: int = 1024
    RERANKER_MODEL: str = ""

    # MinerU 文档解析
    MINERU_API_URL: str = ""

    # 管理员白名单（JSON 数组字符串，pydantic-settings 自动解析）
    ADMIN_USER_LIST: list[str] = []

    # 管理 API 鉴权
    ADMIN_API_TOKEN: str = ""

    # Qdrant
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_COLLECTION_NAME: str = "wechat_rag_kb"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

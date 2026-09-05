"""应用配置：从根目录 .env 读取环境变量并暴露 settings 单例。"""

from functools import lru_cache

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )
    app_name: str = "rag-knowledge-base"
    log_level: str = "INFO"

    # 逗号分隔的前端来源；通过 cors_origin_list 拿到拆分后的列表
    # 之所以用 str 而不是 list[str]：pydantic-settings 对 list 默认按 JSON 解析，
    # 在 .env 里用逗号分隔写 "http://a,http://b" 会报错
    cors_origins: str = "http://localhost:5173"

    # 数据库连接串：注意使用 asyncpg driver
    database_url: str = "postgresql+asyncpg://rag:rag@localhost:5432/rag_kb"

    # ===== 腾讯云 COS 配置 =====
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = "ap-guangzhou"
    cos_bucket: str = ""
    # ===== Embedding（DashScope OpenAI 兼容协议）=====
    embedding_api_key: str = ""
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v3"
    # 维度需与 alembic 迁移中 Vector(N) 保持一致；改维度需要重建表
    embedding_dim: int = 1024
    embedding_batch_size: int = 10
    # ===== 文档上传与切分 =====
    upload_max_size_mb: int = 50
    chunk_size: int = 600
    chunk_overlap: int = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cos_configured(self) -> bool:
        return bool(self.cos_secret_id and self.cos_secret_key and self.cos_bucket)

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
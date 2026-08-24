"""全局配置：通过环境变量注入，前缀 XJT_。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="XJT_", env_file=".env", extra="ignore")

    # 应用
    app_name: str = "校捷通"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # C++ 数据访问层（jt_db 连接池）
    db_host: str = "127.0.0.1"
    db_port: int = 3307  # 本机 MySQL 实例运行在 3307（非默认 3306），按实际修改
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "xiaojietong"
    db_min_conn: int = 2
    db_max_conn: int = 16

    # 安全（JWT）
    jwt_secret: str = "xjt-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 7200          # 2h
    jwt_refresh_expire_seconds: int = 604800  # 7d

    # 微信登录（真实接入需填 appid/secret；留空则用模拟 openid）
    wx_appid: str = ""
    wx_secret: str = ""

    # AI 推理服务（Ollama）
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "xjt-model"

    # RAG 检索增强
    rag_embed_model: str = "bge-m3"      # 向量化模型（Ollama /api/embed）
    rag_embed_dim: int = 1024            # bge-m3 输出维度（用于向量库一致性校验）
    rag_chunk_size: int = 600            # 分块目标字符数（README 规划 500~800）
    rag_chunk_overlap: int = 100         # 分块重叠字符数
    rag_embed_batch: int = 16            # 批量向量化每批条数
    rag_top_k: int = 3                   # 默认检索条数
    rag_score_threshold: float = 0.35    # 相似度阈值（低于则视为未收录）
    rag_vector_dir: str = "data/rag"     # 向量库持久化目录（相对 backend/）


settings = Settings()

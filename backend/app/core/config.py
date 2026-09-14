"""全局配置：通过环境变量注入，前缀 XJT_。"""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定按 **backend/ 目录** 解析（而非当前工作目录）：
# 这样无论从哪个目录启动 uvicorn，或执行 `python -m app.cli.kb_import`
# 之类的命令行工具，都能读到同一份本机配置（DB 端口/密码等）。
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# 默认 JWT 密钥：仅供本地开发。长度 ≥32 字节，以满足 PyJWT 对 HS256 的建议下限
# （旧值 "xjt-dev-secret-change-me" 仅 24 字节，会触发 InsecureKeyLengthWarning）。
_DEFAULT_JWT_SECRET = "xjt-dev-secret-change-me-at-least-32b"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XJT_", env_file=str(_ENV_FILE), extra="ignore"
    )

    # 应用
    app_name: str = "校捷通"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # 运行环境：dev（默认，方便本地开发）/ prod（启用安全硬校验，见 _validate_security）
    env: str = "dev"

    # C21 适配器配置：当前生效的学校配置名（backend/app/adapters/configs/<name>.yml）。
    # 「一份配置描述一所学校」——换学校只改这一项（或直接给文件路径），不改代码。
    # 常用：python -m app.adapters --check 校验 configs/ 下全部学校配置。
    school_config: str = "xiaojietong"

    # C++ 数据访问层（jt_db 连接池）
    db_host: str = "127.0.0.1"
    db_port: int = 3307  # 本机 MySQL 实例运行在 3307（非默认 3306），按实际修改
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "xiaojietong"
    # P0/CON-01：池上限必须 ≥ FastAPI 同步线程池上限（AnyIO 默认 40），
    # 否则高并发下第 17~40 个请求会等 5s 后超时 500。
    db_min_conn: int = 4
    db_max_conn: int = 32

    # 安全（JWT）
    # ⚠️ 生产必须通过 XJT_JWT_SECRET 注入 ≥32 字节随机密钥；
    #    XJT_ENV=prod 时若仍为默认值/长度不足，服务将**拒绝启动**。
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 7200          # 2h
    jwt_refresh_expire_seconds: int = 604800  # 7d

    # 内部联调访问闸门（见 app/core/access_gate.py）。
    # 留空 = 关闭（本机开发默认，行为与之前一致）；
    # 公网 IP 暴露期间**必须设为随机长串**，否则任何人可访问测试接口。
    # 团队调用方式：请求头 X-Access-Token: <token>（或 ?access_token=<token>）
    access_token: str = ""
    # 豁免路径（逗号分隔）—— 只放行**基础存活探针**供部署脚本/监控使用。
    # ⚠️ 刻意**不**豁免 /api/v1/health/detail 与 /health/selfcheck：
    #    它们会回显数据库/连接池/Ollama 状态，属信息泄露，陌生人不应看到。
    # 注意：健康检查挂在 api_prefix 下，完整路径为 /api/v1/health。
    access_gate_exempt: str = "/api/v1/health"

    # 微信登录（真实接入需填 appid/secret；留空则仅开发态可用模拟 openid）
    wx_appid: str = ""
    wx_secret: str = ""

    # CORS（审计 SEC-13）：逗号分隔的允许来源。
    # 本项目鉴权走 Authorization: Bearer，不依赖 Cookie，因此 main.py 固定
    # allow_credentials=False；旧配置 allow_origins=["*"] + credentials=True
    # 会让 Starlette 回显任意 Origin，等价于对全网站点开放。
    # 生产环境（XJT_ENV=prod）不允许使用 * 通配，见 _validate_security。
    cors_origins: str = "*"

    # 接口限流（审计 SEC-10）：单进程内存滑动窗口。
    # 注意：多 worker 部署时每个 worker 独立计数，如需全局限流请接 Redis。
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 300         # 单 IP 全局上限（次/分钟）
    rate_limit_login_per_minute: int = 10    # 登录/刷新单独收紧，防口令与 code 爆破

    # AI 推理服务（Ollama）
    ollama_base_url: str = "http://127.0.0.1:11434"
    # 校园领域微调模型（成员3 C5 产出）。未部署微调模型时可设为 qwen2.5:3b 使用基座模型。
    # 部署方式见 docs/模型分发与部署.md（一键脚本 ai/finetune/deploy_ollama.ps1）。
    ollama_model: str = "xjt-3b"

    # RAG 检索增强
    rag_embed_model: str = "bge-m3"      # 向量化模型（Ollama /api/embed）
    rag_embed_dim: int = 1024            # bge-m3 输出维度（用于向量库一致性校验）
    rag_chunk_size: int = 600            # 分块目标字符数（README 规划 500~800）
    rag_chunk_overlap: int = 100         # 分块重叠字符数
    rag_embed_batch: int = 16            # 批量向量化每批条数
    rag_top_k: int = 3                   # 默认检索条数
    rag_score_threshold: float = 0.35    # 相似度阈值（低于则视为未收录）
    # C16 检索重排：none（默认，不改变现有顺序）/ lexical（IDF+标题加权的词法重排）
    # 实现见 app/services/rerank.py；填了未注册的名字会回退 none 并打 WARNING
    rag_rerank: str = "none"
    # C16 重排前先「多召」多少条候选：候选越多，标题/词法证据越充分，
    # 但也越贵。3 篇候选时 IDF 基本失效（见 rerank.py 的口径说明），故默认 20。
    rag_rerank_candidates: int = 20
    rag_vector_dir: str = "data/rag"     # 向量库持久化目录（相对 backend/）

    # ---------- 文件存储与上传访问（B14）----------
    # local=本地文件系统；s3/oss=对象存储（接口已预留，接入 SDK 后启用）
    storage_backend: str = "local"
    # 上传资源访问签名（默认开启）：/static/uploads 需携带 ?e=过期&s=签名 才能访问，
    # 防止任意人枚举/盗链；上传接口返回的 url 会自动附带签名。
    upload_signed_url_enabled: bool = True
    upload_url_ttl_seconds: int = 604800      # 签名有效期（7 天）
    upload_url_secret: str = ""               # 留空则从 jwt_secret 派生

    # ---------- 异步任务（B15：Celery + Redis）----------
    # false=无 Redis 环境：eager 就地同步执行（行为与旧实现一致，链路已接 Celery）；
    # true=真异步：需启动 Redis 与 worker——
    #   celery -A app.core.celery_app:celery_app worker -l info -P solo
    celery_enabled: bool = False
    celery_broker_url: str = "redis://127.0.0.1:6379/0"
    celery_result_backend: str = "redis://127.0.0.1:6379/1"

    # ---------- 分层推送调度（B18：待办到期前 D-7 / D-2 主动触达）----------
    # 定时任务在到期前 7 天、2 天生成投递记录（notice_delivery），可加 D0（当天/逾期）。
    # 未启用 Celery 时由管理端接口 POST /admin/notices/dispatch 手动触发（eager 同步执行）。
    notice_push_enabled: bool = True
    notice_push_stages: str = "D7,D2"     # 逗号分隔；可选值 D7/D2/D0
    notice_push_hour: int = 8             # 每日调度时点（Asia/Shanghai）
    notice_push_minute: int = 0
    notice_push_channel: int = 1          # 投递渠道：1 站内 2 小程序订阅消息 3 短信
    notice_push_max_items: int = 500       # 单次调度的待办条数上限
    notice_push_max_fanout: int = 500      # 单条通知最多投递用户数
    reminder_auto_done: bool = False      # 到期推送后是否把 reminder.is_done 置 1（默认不代用户改状态）

    # ---------- 知识库批量导入（B17）----------
    kb_import_state_file: str = "data/kb_import_state.json"   # 断点续传状态文件（相对 backend/）
    kb_import_batch_size: int = 200       # 单批入库篇数（配合 --batch-size 覆盖）

    # ---------- 派生属性 ----------

    @property
    def is_prod(self) -> bool:
        """是否生产环境（XJT_ENV=prod/production）。"""
        return self.env.strip().lower() in ("prod", "production")

    @property
    def jwt_secret_is_default(self) -> bool:
        """是否仍在用内置默认密钥（供 /health 暴露，便于运维发现）。"""
        return self.jwt_secret == _DEFAULT_JWT_SECRET

    @property
    def wx_login_mock(self) -> bool:
        """未配置微信凭据 → 走 mock openid（仅开发态允许）。"""
        return not (self.wx_appid and self.wx_secret)

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS 允许来源列表（逗号分隔；空值回退为 *）。"""
        items = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return items or ["*"]

    @property
    def notice_push_stage_list(self) -> list[str]:
        """B18 启用档位（大小写不敏感，非法值忽略；全非法时回退 D7,D2）。"""
        valid = {"D7", "D2", "D0"}
        items = [s.strip().upper() for s in self.notice_push_stages.replace("，", ",").split(",")]
        return [s for s in items if s in valid] or ["D7", "D2"]

    # ---------- 启动校验 ----------

    @model_validator(mode="after")
    def _validate_security(self) -> "Settings":
        """生产环境（XJT_ENV=prod）下强制关键安全配置，带病配置直接拒绝启动。

        - 审计 SEC-01：默认 JWT 密钥公开在仓库中，可被用于伪造任意用户/管理员 token；
        - 审计 SEC-02：未配微信凭据时 /auth/wechat-login 退化为可伪造的 mock 登录。
        开发态保持原行为（仅打 WARNING），不影响本地联调。
        """
        if not self.is_prod:
            return self

        if self.jwt_secret_is_default or len(self.jwt_secret.encode("utf-8")) < 32:
            raise ValueError(
                "生产环境（XJT_ENV=prod）必须设置 XJT_JWT_SECRET 为 ≥32 字节的随机密钥，"
                "当前仍为内置默认值或长度不足。"
                "生成示例：python -c \"import secrets;print(secrets.token_urlsafe(48))\""
            )
        if self.wx_login_mock:
            raise ValueError(
                "生产环境（XJT_ENV=prod）必须配置 XJT_WX_APPID / XJT_WX_SECRET，"
                "否则 /auth/wechat-login 会退化为任何人可伪造的 mock 登录。"
            )
        if "*" in self.cors_origin_list:
            raise ValueError(
                "生产环境（XJT_ENV=prod）必须显式配置 XJT_CORS_ORIGINS（逗号分隔），"
                "不允许使用 * 通配（审计 SEC-13）。"
            )
        if not self.rate_limit_enabled:
            raise ValueError(
                "生产环境（XJT_ENV=prod）必须开启 XJT_RATE_LIMIT_ENABLED（审计 SEC-10）。"
            )
        return self


settings = Settings()

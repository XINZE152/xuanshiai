"""Environment-backed application settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from ``.env`` and process environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Xuanshi AI API"
    app_version: str = "0.1.0"
    environment: Literal["development", "testing", "staging", "production"] = "development"
    debug: bool = True
    docs_enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    api_prefix: str = "/api/v1"
    auto_init_db: bool = True

    database_url: str = "mysql+aiomysql://root:YOUR_MYSQL_PASSWORD@127.0.0.1:3306/xuanshiai"
    redis_url: str = "redis://127.0.0.1:6379/0"

    secret_key: str = "change-me-in-local-env"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    max_sessions_per_user: int = 5
    sms_code_expire_seconds: int = 300
    sms_send_interval_seconds: int = 60
    sms_daily_limit: int = 10
    sms_mock_code: str = "123456"
    wechat_app_id: str | None = None
    wechat_app_secret: str | None = None
    wechat_provider: str = "wechat"
    wechat_payment_mode: Literal["mock", "real"] = "mock"
    # D-21：支付回调验签密钥。real 模式必须显式配置；mock 模式使用
    # wechat_payment_mock_secret（仅开发/测试，生产禁止 mock）。
    wechat_payment_sign_key: SecretStr | None = None
    wechat_payment_mock_secret: str = "mock-payment-secret"

    # ── 微信支付 APIv3 网关（新链路）────────────────────────────────
    # 与 legacy ``wechat_payment_mode`` 并存：legacy 只服务旧 mock 回调，
    # 新网关使用 ``wechat_pay_mode``，便于灰度切换与回滚。
    # disabled=不启用（默认）；mock=本地联调；real=真实微信支付，启动时强校验。
    wechat_pay_mode: Literal["disabled", "mock", "real"] = "disabled"
    wechat_pay_base_url: str = "https://api.mch.weixin.qq.com"
    wechat_pay_mch_id: str | None = None
    wechat_pay_app_id: str | None = None
    wechat_pay_serial_no: str | None = None
    # 商户 API 私钥：可直接放环境变量，也可只配置只读文件路径。
    # 两者同时配置时以 ``wechat_pay_private_key`` 为准。
    wechat_pay_private_key: SecretStr | None = None
    wechat_pay_private_key_path: str | None = None
    # APIv3 密钥固定 32 字节，用于 AES-256-GCM 解密回调 resource。
    wechat_pay_api_v3_key: SecretStr | None = None
    wechat_pay_notify_url: str | None = None
    wechat_pay_refund_notify_url: str | None = None
    # 公钥模式（与平台证书模式二选一）：配置后不再下载平台证书。
    wechat_pay_public_key_id: str | None = None
    wechat_pay_public_key: SecretStr | None = None
    wechat_pay_timeout_seconds: float = Field(default=10, gt=0, le=60)
    wechat_pay_max_clock_skew_seconds: int = Field(default=300, ge=0, le=3600)

    # D-14：人脸认证供应商。disabled 时门禁 fail-closed（不可发帖），
    # mock 仅允许 development/testing。
    face_provider: str = "disabled"
    face_mock_pass: bool = True
    wechat_mock_openid_prefix: str = "mock-openid-"
    sms_provider: str = "disabled"
    agreement_versions_raw: str = (
        "user_service:v1,privacy_policy:v1,safety_pledge:v1,community_rules:v1"
    )

    cors_origins_raw: str = "http://localhost:3000,http://localhost:5173"
    upload_dir: str = "storage/uploads"
    public_base_url: str = "http://127.0.0.1:8000"
    wechat_mini_program_page: str = "pages/profile/profile"
    recommendation_page_size: int = 20
    browse_daily_limit: int = 8
    browse_high_match_bonus: int = 5
    apply_daily_free_limit: int = 3
    apply_daily_vip_limit: int = 10
    # 社区同城浏览偏好变更冷却（天）；对齐 PRD 居住城市一周更新一次精神
    community_city_cooldown_days: int = Field(default=7, ge=0)
    # 红娘服务只能通过现金订单获得，不为新用户自动发放免费次数。
    matchmaker_service_default_quota: int = 0
    superlike_daily_free_limit: int = 1
    superlike_daily_vip_limit: int = 3
    paper_plane_daily_limit: int = 3
    # 测试环境专用：放开「发现/推荐/搜索/详情」链路的异性过滤与资料完整度门禁，
    # 让测试号（含空资料、含同性）互相可见。仅在 development/testing 生效。
    discovery_skip_gates: bool = False

    live_enabled: bool = False
    live_provider: Literal["mock", "tencent"] = "mock"
    tencent_live_sdk_app_id: int | None = Field(default=None, ge=1)
    tencent_live_sdk_secret_key: SecretStr | None = None
    tencent_live_secret_id: str | None = None
    tencent_live_secret_key: SecretStr | None = None
    tencent_live_region: str = "ap-guangzhou"
    tencent_live_api_timeout_seconds: float = Field(default=5, gt=0, le=30)
    tencent_live_max_retries: int = Field(default=2, ge=0, le=5)
    tencent_live_user_sig_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    live_rtc_ticket_limit_per_minute: int = Field(default=10, ge=1, le=120)
    tencent_live_callback_secret: SecretStr | None = None
    tencent_live_callback_max_skew_seconds: int = Field(default=300, ge=0, le=3600)
    tencent_live_callback_max_body_bytes: int = Field(default=65536, ge=1024, le=1048576)
    tencent_live_callback_event_types_raw: str = ""
    tencent_live_cloud_recording_enabled: bool = False
    tencent_live_mix_stream_enabled: bool = False
    tencent_live_css_enabled: bool = False

    # ── 后台运营第三方服务商适配层（密钥仅走环境变量，管理端接口不回显明文）──
    # 短信模板群发（A1）：厂商未定前保持 disabled，管理端接口 503 明确报错。
    sms_template_provider: str = "disabled"
    sms_access_key: str | None = None
    sms_access_secret: SecretStr | None = None
    # 微信公众号平台（A2）：凭据通过环境变量注入，库内 wechat_mp 配置只存非敏感项。
    wechat_mp_app_id: str | None = None
    wechat_mp_app_secret: SecretStr | None = None
    wechat_mp_token: SecretStr | None = None
    wechat_mp_aes_key: str | None = None
    # 腾讯电子签（A3）：无凭证时合同创建/签署/作废接口 503。
    tencent_esign_secret_id: str | None = None
    tencent_esign_secret_key: SecretStr | None = None
    tencent_esign_operator_open_id: str | None = None
    # 电话外呼平台（A6）：捷讯 API 未定前保持 disabled。
    outbound_call_provider: str = "disabled"
    outbound_call_base_url: str | None = None
    outbound_call_account: str | None = None
    outbound_call_secret: SecretStr | None = None
    outbound_call_timeout_seconds: float = Field(default=10, gt=0, le=60)
    # 核验额度在线充值（A17）：厂商未定前保持 disabled，下单接口 503；
    # mock 仅用于开发/联调（生成待付订单 + 模拟支付链接），生产禁止。
    # 套餐目录只读展示不受该开关影响。
    verification_recharge_provider: Literal["disabled", "mock", "real"] = "disabled"

    # 属地解析（A12-一键刷新 IP/手机属地）：全项目当前无 IP→属地 / 号码归属地解析器（外部依赖）。
    # disabled（默认）→ 刷新接口 503；mock → 干跑（只统计候选行、不写库，adapter_implemented=false）；
    # real → 预留（适配器未接入时返回 501）。预发/生产禁止 mock。
    region_resolver_provider: Literal["disabled", "mock", "real"] = "disabled"

    # AI avatar calls an OpenAI-compatible chat-completions service from the
    # backend only. API keys must never be exposed to the mini-program.
    ai_avatar_provider: Literal["disabled", "openai_compatible"] = "disabled"
    ai_avatar_base_url: str | None = None
    ai_avatar_api_key: SecretStr | None = None
    ai_avatar_model: str | None = None
    ai_avatar_timeout_seconds: float = Field(default=20, gt=0, le=60)
    ai_avatar_max_output_tokens: int = Field(default=500, ge=64, le=2000)
    ai_avatar_max_context_messages: int = Field(default=12, ge=2, le=30)
    ai_avatar_daily_limit: int = Field(default=20, ge=1, le=200)

    # Optional second-layer text moderation. The provider is disabled until
    # the purchased marketplace API path and AppCode are configured.
    aliyun_content_moderation_enabled: bool = False
    aliyun_content_moderation_base_url: str = (
        "https://lxmingan.market.alicloudapi.com"
    )
    aliyun_content_moderation_path: str = "/YOUR_API_PATH"
    aliyun_content_moderation_app_code: SecretStr | None = None
    aliyun_content_moderation_request_mode: Literal["json", "form"] = "json"
    aliyun_content_moderation_text_field: str = "text"
    aliyun_content_moderation_timeout_seconds: float = Field(
        default=2.5, gt=0, le=10
    )
    aliyun_content_moderation_fail_mode: Literal["review", "reject"] = "review"
    aliyun_content_moderation_default_action: Literal[
        "manual_review", "reject", "replace"
    ] = "manual_review"

    # Optional environment overrides for commercial configuration. When unset,
    # the corresponding database configuration remains the fallback.
    membership_monthly_price: float | None = Field(default=None, ge=0)
    membership_quarterly_price: float | None = Field(default=None, ge=0)
    membership_yearly_price: float | None = Field(default=None, ge=0)
    membership_monthly_original_price: float | None = Field(default=None, ge=0)
    membership_quarterly_original_price: float | None = Field(default=None, ge=0)
    membership_yearly_original_price: float | None = Field(default=None, ge=0)
    membership_monthly_daily_price: float | None = Field(default=None, ge=0)
    membership_quarterly_daily_price: float | None = Field(default=None, ge=0)
    membership_yearly_daily_price: float | None = Field(default=None, ge=0)

    # Rewards are also configurable so all point values have one source.
    point_checkin_reward: int = Field(default=5, gt=0)
    point_profile_complete_reward: int = Field(default=50, gt=0)
    point_realname_verified_reward: int = Field(default=100, gt=0)

    # Per-use costs for point products. Unset values fall back to the product
    # row, allowing existing database-configured products to keep working.
    point_cost_extra_apply: int | None = Field(default=None, gt=0)
    point_cost_extra_superlike: int | None = Field(default=None, gt=0)
    point_cost_browse_unlock: int | None = Field(default=None, gt=0)
    point_cost_exposure_card: int | None = Field(default=None, gt=0)
    point_cost_paper_plane_unlock: int | None = Field(default=None, gt=0)
    point_cost_profile_detail_unlock: int | None = Field(default=None, gt=0)
    point_cost_membership_exchange: int | None = Field(default=None, gt=0)
    point_cost_service_coupon: int | None = Field(default=None, gt=0)

    # ==================== AI 功能开关与门禁 ====================
    # 一期全部默认关闭。生产环境只有在 ai_policy_approved、
    # ai_provider_approved、ai_retention_policy_version 同时满足且
    # Provider 不是 mock 时才允许打开（见 validate_ai_feature_gates）。
    ai_master_enabled: bool = False
    ai_profile_enabled: bool = False
    ai_search_enabled: bool = False
    ai_compatibility_shadow_enabled: bool = False
    ai_recommend_enabled: bool = False
    # 画像发布门槛：至少确认多少个字段才允许 publish（提前建构阈值）。
    # 良配对齐：默认 7/10 ≈ 67%，"无需完成全部题目，进度 67% 左右可提前
    # 建构画像"。进度提示与发布硬门槛共用此值，避免两套数字漂移。
    ai_profile_min_fields: int = Field(default=7, ge=1, le=20)
    # 匹配度外显灰度（方案 WP-C2 / 决策 D6）：off=影子运行不外显（现状）；
    # bucket=按 viewer 稳定哈希放量 ai_compatibility_display_bucket_pct%；
    # on=全量外显。仅改变 ai_compatibility_snapshot.display_eligible 的写入值，
    # 读取端门禁（_apply_display_gate）与 shadow 纪律测试在 off 下保持不变。
    ai_compatibility_display_mode: Literal["off", "bucket", "on"] = "off"
    ai_compatibility_display_bucket_pct: int = Field(default=0, ge=0, le=100)
    # 记忆投影读取切换（Phase 2）：legacy=只读旧 ai_feature_projection（默认）；
    # shadow=双读但结果以旧链路为准并记录 canonical diff；memory=只读
    # ai_memory_projection（缺失时按固定策略回退 legacy）。非法值在启动时
    # 由 Literal 校验直接失败（fail fast）。
    ai_memory_projection_read_mode: Literal["legacy", "shadow", "memory"] = "legacy"
    # 一期默认 mock；deepseek 为首个真 provider（开发/测试可用，生产启用需
    # 先满足 ai_policy_approved / ai_provider_approved / retention 三道门禁）。
    # dots 为小红书 hi lab dots.llm 的 OpenAI 兼容 API provider。
    ai_provider: Literal["mock", "deepseek", "dots"] = "mock"
    # 生产启用门禁（Task 1 冻结）：缺任一批准项则校验失败。
    ai_policy_approved: bool = False
    ai_provider_approved: bool = False
    ai_retention_policy_version: str | None = None

    # 测试期 AI 用户隔离：为空表示不限制；非空（逗号分隔的 user_id）时，
    # 只有这些用户允许调用 AI 功能，其余用户按"功能未开放"拒绝。
    # 用途：临时接入第三方免费端点做联调时，确保真实用户的数据不会被发出去。
    ai_test_only_user_ids: str = ""

    # DeepSeek provider 配置（OpenAI 兼容 API）。真实 api_key 仅存于被忽略的
    # .env；.env.example 只放占位符 YOUR_DEEPSEEK_API_KEY。生产启用需先走
    # Provider 审批门禁（ai_policy_approved 等）。deepseek-chat 已弃用，
    # 由 deepseek-v4-flash 接替。
    ai_deepseek_api_key: SecretStr | None = None
    ai_deepseek_base_url: str = "https://api.deepseek.com"
    ai_deepseek_model: str = "deepseek-v4-flash"
    # 2048 装不下用心度评审这类长 JSON 输出（summary 500 字 + 最多 4 条
    # advice 各 300 字 ≈ 1700 汉字，中文按 1.5~2 token/字折算超 2500 token），
    # 输出被截断会让 parse_json 收到不完整 JSON 而 503。默认给到 4096。
    ai_deepseek_max_tokens: int = Field(default=4096, gt=0, le=8192)

    # Dots provider 配置（小红书 hi lab dots.llm，OpenAI 兼容 API）。真实
    # api_key 仅存于被忽略的 .env；.env.example 只放占位符。dots3-note-prev
    # 是推理模型（返回 reasoning_content + content），max_tokens 需覆盖推理
    # 消耗，默认给到 4096。
    ai_dots_api_key: SecretStr | None = None
    ai_dots_base_url: str = "https://note3-prev-api.askdiandian.com/v1"
    ai_dots_model: str = "dots3-note-prev"
    ai_dots_max_tokens: int = Field(default=4096, gt=0, le=8192)

    # Narrative 专用模型覆盖（可选）。画像叙事层 generate_narrative 是重推理
    # 任务，默认走主 provider 的模型。若主 provider 是推理模型（如 dots3-note-prev），
    # 生成耗时可达数十秒；通过此项指定一个更快的非推理模型 + 配套 provider 来
    # 单独驱动 narrative，其余方法（抽取/搜索/回复）不受影响。
    # 为空时回退到主 provider 默认模型。需要配套的 provider key 可用。
    ai_narrative_provider: Literal["", "deepseek", "dots"] = ""
    ai_narrative_model: str = ""
    ai_narrative_max_tokens: int = Field(default=0, ge=0, le=8192)

    # ==================== 语音（STT/TTS）功能开关与配置 ====================
    # P-04 / Phase 4。默认关闭。生产启用需满足三道审批门禁 + AccessKey 配置
    # （见 _validate_ai_feature_gates 的 fail-closed 检查）。
    ai_voice_enabled: bool = False
    ai_voice_provider: Literal["aliyun"] = "aliyun"
    # 阿里云智能语音交互（NLS）配置。api_key/app_key 仅存于被忽略的 .env，
    # 不进 .env.example；生产启用需先走语音 Provider 审批 + DPA / 数据出境审查。
    ai_aliyun_voice_api_key: SecretStr | None = None
    ai_aliyun_voice_app_key: SecretStr | None = None
    ai_aliyun_voice_region: str = "cn-shanghai"
    ai_aliyun_voice_asr_model: str = "paraformer-realtime-v2"
    ai_aliyun_voice_tts_model: str = "cosyvoice-v1"
    # 语音合成单次文本上限（与 voice/base.MAX_TTS_TEXT_LENGTH 对齐）。
    ai_tts_max_text_length: int = Field(default=500, gt=0, le=2000)
    # 语音转写单次音频时长上限（秒，与前端录音 60s 上限对齐）。
    ai_asr_max_duration_seconds: int = Field(default=60, gt=0, le=300)
    # 临时音频文件过期清理（小时），合规要求转写后短期保留即删除。
    # 音频 retention 与 voice_transcript retention 分开配置，禁止混用。
    ai_voice_audio_retention_hours: int = Field(default=24, gt=0)
    # REST ASR 的转写原文保留期。它是数据库文本，不是临时音频文件；到期后由
    # worker 从 voice_transcript 删除，且不会影响 ai_memory_state 的逐行 TTL。
    ai_voice_transcript_retention_hours: int = Field(default=72, gt=0)
    # worker 主循环执行语音音频清理的最小间隔（秒）。0 表示每轮都尝试。
    ai_voice_audio_cleanup_interval_seconds: int = Field(default=3600, ge=0)

    # ==================== Memory State TTL 定时清理（Batch-1 Task 2）====================
    # 单批过期的 ai_memory_state 行数上限（expire_memory_states 的 limit）。
    ai_memory_state_ttl_batch_size: int = Field(default=200, ge=1)
    # 单轮最多执行的批次数上限（每批独立会话/独立提交）。
    ai_memory_state_ttl_max_batches: int = Field(default=10, ge=1)
    # 单轮墙钟时间上限（秒）：每批开始前检查，预算耗尽即停、剩余留给下一轮。
    ai_memory_state_ttl_time_budget_seconds: float = Field(default=30.0, gt=0)
    # worker 主循环执行 Memory State TTL 清理的最小间隔（秒）。0 表示每轮都尝试。
    ai_memory_state_ttl_cleanup_interval_seconds: int = Field(default=3600, ge=0)

    # ==================== AI 文本/审计/outbox retention（Task 9）====================
    # 成功 outbox 从 occurred_at 起保留一周；dead-letter 从 dead_letter_at 起
    # 保留 30 天。pending/processing 永不由 retention 任务删除，仍由消费者的
    # 有限重试、租约回收和 dead-letter 状态机负责。
    ai_derivation_outbox_succeeded_retention_hours: int = Field(default=168, gt=0)
    ai_derivation_outbox_dead_letter_retention_hours: int = Field(default=720, gt=0)
    # Provider 审计不含 prompt/response，但保留最小调用元数据供追责与计费复核。
    ai_generation_audit_retention_hours: int = Field(default=2160, gt=0)
    # 每张表每轮最多删除的行数，以及 worker 定时执行的最小间隔。
    ai_retention_cleanup_batch_size: int = Field(default=200, ge=1)
    ai_retention_cleanup_interval_seconds: int = Field(default=3600, ge=0)

    # ==================== 实时半双工语音对话（P-04b）====================
    # 实时对话模式开关：默认关闭。生产环境 fail closed（见
    # ``_validate_ai_feature_gates``）：需满足三道审批门禁 + AccessKey 配置。
    # 实时 ASR 鉴权需要 AccessKey ID/Secret（不只是 api_key/app_key），
    # 用以换取 NLS Token；该凭据仅存于被忽略的 .env，不进 .env.example。
    ai_voice_conversation_enabled: bool = False
    ai_aliyun_voice_access_key_id: SecretStr | None = None
    ai_aliyun_voice_access_key_secret: SecretStr | None = None
    # 单次实时对话轮次最长音频时长（秒），与前端实时录音上限对齐。
    ai_voice_conversation_max_turn_seconds: int = Field(default=60, gt=0, le=300)
    # 实时 ASR WebSocket 接入点（阿里云 NLS 实时语音识别）。
    # 注意必须是连字符域名 nls-gateway-cn-shanghai（官方文档标准）：
    # 点分域名 nls-gateway.cn-shanghai 能握手但引擎不产出任何识别结果。
    ai_aliyun_voice_asr_ws_url: str = (
        "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"
    )

    # ==================== 实时全双工语音对话（SenseAudio，v2 协议）====================
    # 墨相师实时语音分支：由端到端实时供应商直接产出语音回复（ASR+LLM+TTS 一体），
    # 画像抽取仍走现有旅程任务。默认关闭；生产环境 fail closed：
    # 需三道审批门禁 + 供应商凭据（见 _validate_ai_feature_gates，按实际启用
    # 能力校验对应凭据，不借用阿里云 AccessKey 门禁）。
    # api_key 仅存于被忽略的 .env，不进 .env.example、不进代码与日志。
    ai_realtime_voice_enabled: bool = False
    # 实时供应商选择；空串表示未配置，v2 协商直接失败。
    ai_realtime_voice_provider: Literal["", "senseaudio"] = ""
    ai_senseaudio_api_key: SecretStr | None = None
    ai_senseaudio_ws_url: str = "wss://api.senseaudio.cn/ws/v1/realtime/voice-dialog"
    ai_senseaudio_model: str = "senseaudio-realtime-1.0"
    # 供应商音色；空串使用供应商默认女声。
    ai_senseaudio_voice: str = ""
    # 会话与额度灰度默认值（方案 §5）：每用户同时 1 条会话（内存计数）、
    # 单次最多 10 分钟、每日最多 20 分钟连接时长、无有效发言 60 秒退出。
    ai_realtime_session_max_minutes: int = Field(default=10, gt=0, le=60)
    ai_realtime_daily_minutes: int = Field(default=20, gt=0, le=600)
    ai_realtime_idle_exit_seconds: int = Field(default=60, gt=0, le=600)
    # 下行放行前的输入审核等待上限（秒）：最终转写提交（含审核）超过该时长
    # 仍未通过时终止本轮回复，绝不放行未经审核的下行内容。
    ai_realtime_submission_hold_seconds: float = Field(default=15.0, gt=0, le=60)

    # AI 任务/租约/重试/限流配置。
    ai_lease_seconds: int = Field(default=300, gt=0, le=3600)
    ai_max_attempts: int = Field(default=3, gt=0, le=10)
    ai_search_parse_rate_per_minute: int = Field(default=5, gt=0, le=60)
    # WP-S3：猜你喜欢 AI 生成频控（每用户 24h 窗口内的任务数上限）。
    ai_search_suggest_daily_limit: int = Field(default=5, gt=0, le=50)
    ai_profile_session_expire_days: int = Field(default=7, gt=0)
    ai_search_draft_expire_hours: int = Field(default=24, gt=0)
    ai_compatibility_snapshot_ttl_minutes: int = Field(default=10, gt=0)
    # WP-C1：llm 精算快照 TTL（默认 7 天）——命中期内二次查看不再触发精算。
    ai_compatibility_llm_ttl_minutes: int = Field(default=10080, gt=0)
    ai_gateway_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    # WP-P6 三类推荐（D4 快照预计算）：物化有效期 / 候选池上限 / 每视图 top-N。
    ai_recommendation_ttl_minutes: int = Field(default=1440, gt=0)
    ai_recommendation_pool_limit: int = Field(default=200, gt=0)
    ai_recommendation_top_n: int = Field(default=20, gt=0)

    # ==================== 墨相师四阶段融合 Journey 开关（Contract v1.1 §10）====================
    # 墨相师新旅程（候选理解池 + 自动整理邀请）默认关闭。生产启用需经过
    # _validate_ai_feature_gates 的三道审批门禁。关闭时不产生新候选、不推
    # 新旅程默认关闭；生产启用前必须通过 _validate_ai_feature_gates 三道审批。
    ai_moxiang_journey_enabled: bool = False

    # Task 12 审计/指标开关（非敏感，不影响 production fail-closed）。
    ai_audit_enabled: bool = True
    # outbox/purge 积压指标触发本地告警的阈值。
    ai_metrics_backlog_warn_threshold: int = Field(default=1000, ge=0)

    log_level: str = "INFO"

    # AI 军师（助手/润色/搜索/匹配，合著仓方案）。与画像/搜索/语音共用
    # 生产 fail-closed：``ai_enabled`` 计入 any_ai_enabled。运行时仍走
    # ``require_ai_feature(AiFeature.ADVISOR)``（master + ai_enabled + 审批门）。
    ai_enabled: bool = False
    # dev/testing 在 ai_enabled=false 时回退本地 mock 的总开关（可观测化）：
    # 置 false 后 dev/testing 的 mock 回退改为 503「AI服务未启用」。生产环境
    # 本就 fail-closed 禁止 mock，不受此开关影响。默认 true 保持既有 dev 行为。
    ai_allow_mock_fallback: bool = True
    ai_base_url: str = "https://api.deepseek.com/v1"
    ai_api_key: SecretStr | None = None
    ai_model: str = "deepseek-chat"
    ai_timeout_seconds: float = Field(default=30, gt=0, le=120)
    ai_max_context_messages: int = Field(default=80, ge=10, le=200)
    ai_daily_assistant_limit: int = Field(default=20, ge=1, le=1000)
    ai_daily_polish_limit: int = Field(default=5, ge=1, le=100)
    ai_daily_search_limit: int = Field(default=10, ge=1, le=100)
    ai_daily_match_limit: int = Field(default=10, ge=1, le=100)
    ai_daily_advisor_limit: int = Field(default=20, ge=1, le=1000)
    ai_daily_thoughtfulness_limit: int = Field(default=10, ge=1, le=100)
    # AI 智能录入（客源文本抽取）：红娘后台按账号的每日调用上限。
    ai_daily_lead_extract_limit: int = Field(default=20, ge=1, le=1000)
    ai_advisor_max_context_messages: int = Field(default=80, ge=10, le=200)
    ai_advisor_prompt_version: str = "relationship-v1"
    ai_advisor_knowledge_version: str = "seed-v2"

    @property
    def cors_origins(self) -> list[str]:
        """Convert the comma-separated environment value into CORS origins."""
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def ai_provider_name(self) -> str:
        """当前生效的 provider 名称，用于 AITaskContext 审计元数据。"""
        return self.ai_provider

    @property
    def ai_test_only_user_id_set(self) -> frozenset[int]:
        """解析 ``ai_test_only_user_ids``；空集合表示不做用户隔离。

        容忍逗号、分号、全角逗号和空白分隔，非数字片段直接忽略（不抛错），
        避免一个手误的字符让服务启动失败。
        """
        raw = (
            (self.ai_test_only_user_ids or "")
            .replace("，", ",")
            .replace("；", ",")
            .replace(";", ",")
        )
        allowed: set[int] = set()
        for part in raw.replace(" ", ",").split(","):
            part = part.strip()
            if part.isdigit():
                allowed.add(int(part))
        return frozenset(allowed)

    @property
    def ai_model_name(self) -> str:
        """当前 provider 对应的模型名，用于 AITaskContext 审计元数据。

        mock 时返回固定占位以保持历史审计一致性；真 provider 返回配置值。
        """
        if self.ai_provider == "deepseek":
            return self.ai_deepseek_model
        if self.ai_provider == "dots":
            return self.ai_dots_model
        return "mock-model-v1"

    @property
    def ai_voice_model_name(self) -> str:
        """语音 provider 对应的模型名，用于语音任务审计元数据。

        STT 与 TTS 模型不同，这里返回 ASR 模型作为代表；TTS 审计由
        VoiceGateway 场景区分。
        """
        return self.ai_aliyun_voice_asr_model

    @property
    def agreement_versions(self) -> dict[str, str]:
        """Return the currently published agreement version for each type."""
        versions: dict[str, str] = {}
        for item in self.agreement_versions_raw.split(","):
            if ":" in item:
                agreement_type, version = item.split(":", 1)
                versions[agreement_type.strip()] = version.strip()
        return versions

    @property
    def is_test_mode(self) -> bool:
        """Return whether development-only providers are allowed."""
        return self.environment in {"development", "testing"}

    @property
    def discovery_skip_gates_enabled(self) -> bool:
        """测试环境专用：是否放开发现链路的性别/完整度门禁（fail-closed）。"""
        return self.is_test_mode and self.discovery_skip_gates

    def membership_price_override(self, code: str, field: str, fallback: float | None) -> float | None:
        """Return an environment override for a membership package field."""
        value = getattr(self, f"membership_{code}_{field}", None)
        return value if value is not None else fallback

    def point_cost_override(self, code: str, fallback: int) -> int:
        """Return the configured per-use cost for a point product."""
        value = getattr(self, f"point_cost_{code}", None)
        return value if value is not None else fallback

    def wechat_pay_private_key_pem(self) -> str | None:
        """读取商户 API 私钥 PEM。

        优先使用 ``WECHAT_PAY_PRIVATE_KEY``；未配置时读取只读文件
        ``WECHAT_PAY_PRIVATE_KEY_PATH``。读取失败直接抛 ValueError，
        由启动校验 fail-closed。
        """
        if self.wechat_pay_private_key is not None:
            value = self.wechat_pay_private_key.get_secret_value().strip()
            return value or None
        path = (self.wechat_pay_private_key_path or "").strip()
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip() or None
        except OSError as exc:
            raise ValueError(f"无法读取 WECHAT_PAY_PRIVATE_KEY_PATH：{path}") from exc

    def wechat_pay_public_key_pem(self) -> str | None:
        """读取微信支付公钥 PEM（公钥模式）。"""
        if self.wechat_pay_public_key is None:
            return None
        value = self.wechat_pay_public_key.get_secret_value().strip()
        return value or None

    def _validate_wechat_pay_gateway(self) -> None:
        """校验微信支付 APIv3 网关配置。

        - ``mock`` 只允许 development/testing；
        - ``real`` 必须一次性配齐商户号、AppID、证书序列号、私钥、APIv3 密钥
          和回调地址，缺少任一项启动失败；
        - staging/production 的回调地址必须是 HTTPS。
        """
        mode = self.wechat_pay_mode
        if mode == "mock" and not self.is_test_mode:
            raise ValueError("staging/production 环境禁止启用微信支付网关 Mock 模式")
        if mode != "real":
            return

        required = {
            "WECHAT_PAY_MCH_ID": self.wechat_pay_mch_id,
            "WECHAT_PAY_APP_ID": self.wechat_pay_app_id,
            "WECHAT_PAY_SERIAL_NO": self.wechat_pay_serial_no,
            "WECHAT_PAY_NOTIFY_URL": self.wechat_pay_notify_url,
        }
        missing = [name for name, value in required.items() if not (value or "").strip()]
        if missing:
            raise ValueError(f"微信支付 real 模式缺少配置：{', '.join(missing)}")

        api_v3_key = (
            self.wechat_pay_api_v3_key.get_secret_value().strip()
            if self.wechat_pay_api_v3_key
            else ""
        )
        if len(api_v3_key.encode("utf-8")) != 32:
            raise ValueError("WECHAT_PAY_API_V3_KEY 必须是 32 字节")
        if not self.wechat_pay_private_key_pem():
            raise ValueError(
                "微信支付 real 模式必须配置 WECHAT_PAY_PRIVATE_KEY 或 WECHAT_PAY_PRIVATE_KEY_PATH"
            )
        if self.wechat_pay_public_key_id and not self.wechat_pay_public_key_pem():
            raise ValueError(
                "配置 WECHAT_PAY_PUBLIC_KEY_ID 时必须同时配置 WECHAT_PAY_PUBLIC_KEY"
            )
        if self.environment in {"staging", "production"}:
            for name, value in (
                ("WECHAT_PAY_NOTIFY_URL", self.wechat_pay_notify_url),
                ("WECHAT_PAY_REFUND_NOTIFY_URL", self.wechat_pay_refund_notify_url),
            ):
                if value and not value.startswith("https://"):
                    raise ValueError(f"{name} 在 staging/production 必须使用 HTTPS")

    @model_validator(mode="after")
    def validate_test_providers(self) -> "Settings":
        """Prevent Mock providers from being enabled in production."""
        if self.environment in {"staging", "production"} and self.auto_init_db:
            raise ValueError("staging/production 环境必须关闭 AUTO_INIT_DB")
        if self.environment in {"staging", "production"} and self.live_provider == "mock":
            raise ValueError("staging/production 环境禁止使用直播 Mock Provider")
        if self.environment in {"staging", "production"} and self.verification_recharge_provider == "mock":
            raise ValueError("staging/production 环境禁止使用核验充值 Mock Provider")
        if self.environment in {"staging", "production"} and self.region_resolver_provider == "mock":
            raise ValueError("staging/production 环境禁止使用属地解析 Mock Provider")
        if self.environment in {"staging", "production"} and self.discovery_skip_gates:
            raise ValueError("staging/production 环境禁止开启 DISCOVERY_SKIP_GATES")
        if self.live_enabled and self.live_provider == "tencent":
            sdk_secret = (
                self.tencent_live_sdk_secret_key.get_secret_value().strip()
                if self.tencent_live_sdk_secret_key
                else ""
            )
            cam_secret = (
                self.tencent_live_secret_key.get_secret_value().strip()
                if self.tencent_live_secret_key
                else ""
            )
            if not self.tencent_live_sdk_app_id or not sdk_secret:
                raise ValueError(
                    "启用腾讯直播 Provider 时必须配置 SDKAppID 和 SDKSecretKey"
                )
            if not (self.tencent_live_secret_id or "").strip() or not cam_secret:
                raise ValueError(
                    "启用腾讯直播 Provider 时必须配置 CAM SecretId 和 SecretKey"
                )
            if (
                not self.tencent_live_callback_secret
                or not self.tencent_live_callback_secret.get_secret_value().strip()
            ):
                raise ValueError("启用腾讯直播 Provider 时必须配置回调密钥")
            if not self.tencent_live_callback_event_types_raw.strip():
                raise ValueError("启用腾讯直播 Provider 时必须配置回调事件白名单")
        if not self.is_test_mode and (
            self.sms_provider == "mock" or self.wechat_provider == "mock" or self.wechat_payment_mode == "mock"
        ):
            raise ValueError("生产环境禁止启用短信、微信登录或微信支付 Mock 服务")
        if self.sms_provider.lower() == "mock" and (
            len(self.sms_mock_code) != 6 or not self.sms_mock_code.isdigit()
        ):
            raise ValueError("SMS_MOCK_CODE 必须是6位数字")
        # A1/A6：运营群发与外呼通道的 Mock 实现只允许开发/测试环境使用。
        if not self.is_test_mode and "mock" in {
            self.sms_template_provider.lower(),
            self.outbound_call_provider.lower(),
        }:
            raise ValueError("生产环境禁止启用短信群发或外呼平台的 Mock 服务")
        if (
            self.environment == "production"
            and self.aliyun_content_moderation_enabled
            and not self.aliyun_content_moderation_app_code
        ):
            raise ValueError(
                "生产环境启用阿里云敏感词服务时必须配置 AppCode"
            )
        self._validate_ai_feature_gates()
        if self.ai_avatar_provider != "disabled":
            if not self.ai_avatar_base_url or not self.ai_avatar_model:
                raise ValueError("启用 AI 分身时必须配置 AI_AVATAR_BASE_URL 和 AI_AVATAR_MODEL")
            if self.environment in {"staging", "production"} and not self.ai_avatar_base_url.startswith(
                "https://"
            ):
                raise ValueError("staging/production 的 AI_AVATAR_BASE_URL 必须使用 HTTPS")
        self._validate_wechat_pay_gateway()
        return self

    def ai_approvals_complete(self) -> bool:
        """Return whether all three production approval gates are satisfied."""
        return bool(
            self.ai_policy_approved
            and self.ai_provider_approved
            and self.ai_retention_policy_version
        )

    def _validate_ai_feature_gates(self) -> None:
        """Fail closed when any AI switch is enabled without the full gates.

        Production only: a real (non-mock) provider must be approved before any
        AI feature may run.  Missing approval flags or a mock production
        provider raise so ``Settings(...)`` construction fails with a
        ``ValidationError``.
        """
        if self.environment != "production":
            return
        any_ai_enabled = any(
            (
                self.ai_master_enabled,
                self.ai_profile_enabled,
                self.ai_search_enabled,
                self.ai_compatibility_shadow_enabled,
                self.ai_recommend_enabled,
                self.ai_voice_enabled,
                # 实时对话/实时语音各自独立开关，必须参与判定，否则仅开
                # 其中一个时整段 fail-closed 校验会被跳过。
                self.ai_voice_conversation_enabled,
                self.ai_realtime_voice_enabled,
                self.ai_moxiang_journey_enabled,
                # legacy 军师直连栈：只开 ai_enabled 也必须走审批门，
                # 不得绕过生产 fail-closed（修复清单 §3.12.4）。
                self.ai_enabled,
                # AI 分身：开启即继承下方共用门禁（SECRET_KEY/DEBUG/DOCS/
                # 三道审批），不得绕过生产 fail-closed。
                self.ai_avatar_provider != "disabled",
            )
        )
        # provider 专项检查（deepseek/dots key 与 mock 禁令）保护的是共享
        # settings.ai_provider——其消费者覆盖 legacy complete() 直连栈与现代栈
        # AIGateway（gateway 直接从 settings 解析 provider），因此卫兵取加入
        # avatar 之前的原十项开关原样：仅 avatar-only 的生产配置跳过这三项
        # （分身走独立的 ai_avatar_base_url/provider），其余一切现存配置的
        # 启动行为逐条不变。
        provider_checks_on = any(
            (
                self.ai_master_enabled,
                self.ai_profile_enabled,
                self.ai_search_enabled,
                self.ai_compatibility_shadow_enabled,
                self.ai_recommend_enabled,
                self.ai_voice_enabled,
                self.ai_voice_conversation_enabled,
                self.ai_realtime_voice_enabled,
                self.ai_moxiang_journey_enabled,
                self.ai_enabled,
            )
        )
        if not any_ai_enabled:
            return
        if self.secret_key.strip() in {"", "change-me-in-local-env"} or len(self.secret_key.strip()) < 32:
            raise ValueError("生产环境启用 AI 必须配置至少 32 位非占位 SECRET_KEY")
        if self.debug or self.docs_enabled:
            raise ValueError("生产环境启用 AI 必须关闭 DEBUG、DOCS_ENABLED")
        if provider_checks_on and self.ai_provider == "deepseek" and not self.ai_deepseek_api_key:
            raise ValueError("生产环境启用 DeepSeek AI 必须配置 AI_DEEPSEEK_API_KEY")
        if provider_checks_on and self.ai_provider == "dots" and not self.ai_dots_api_key:
            raise ValueError("生产环境启用 Dots AI 必须配置 AI_DOTS_API_KEY")
        if not self.ai_approvals_complete():
            raise ValueError(
                "生产环境启用 AI 功能必须同时满足 ai_policy_approved、"
                "ai_provider_approved 和 ai_retention_policy_version"
            )
        if provider_checks_on and self.ai_provider == "mock":
            raise ValueError("生产环境禁止使用 mock AI Provider")
        if self.ai_voice_conversation_enabled:
            # 实时对话模式同样需要三道审批门禁。
            if not self.ai_approvals_complete():
                raise ValueError(
                    "生产环境启用实时语音对话必须同时满足 ai_policy_approved、"
                    "ai_provider_approved 和 ai_retention_policy_version"
                )
            # 实时 ASR 需要 AccessKey 鉴权（换取 NLS Token），与 REST 模式的
            # api_key/app_key 不同：缺 AccessKey 直接 fail closed。
            if (
                not self.ai_aliyun_voice_access_key_id
                or not self.ai_aliyun_voice_access_key_secret
            ):
                raise ValueError(
                    "生产环境启用实时语音对话必须配置 AccessKey ID/Secret"
                )
        if self.ai_realtime_voice_enabled:
            # 实时语音 v2 按实际启用的能力校验对应凭据：SenseAudio 用自己的
            # api_key，不借用阿里云 AccessKey 门禁；旧 STT/TTS 启用时仍走
            # 上面的原凭据检查。
            if self.ai_realtime_voice_provider != "senseaudio":
                raise ValueError(
                    "生产环境启用实时语音必须配置 ai_realtime_voice_provider"
                )
            if not self.ai_senseaudio_api_key:
                raise ValueError(
                    "生产环境启用实时语音必须配置 SenseAudio api_key"
                )


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for dependency injection."""
    return Settings()


settings = get_settings()

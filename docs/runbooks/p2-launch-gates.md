# P2 开闸与外部依赖 Runbook（2026-11-09）

> 任务标识：`UT-2026-11-02-DOC-02-BE-20261109`
> 本文覆盖 AI、短信、人脸/实名的开闸检查与 Q51 超时定位。**不在仓库写入任何真实 Key/Token**；所有凭证只放服务器 `.env` 或密钥管理。

## 1. AI 批次（Q18/Q24/Q40/Q41/Q49/Q50/Q62/Q78）

### 1.1 前置审批

| 配置 | 要求 | 说明 |
| --- | --- | --- |
| `AI_POLICY_APPROVED` | `true` | 合规审批 |
| `AI_PROVIDER_APPROVED` | `true` | Provider 审批 |
| `AI_RETENTION_POLICY_VERSION` | 非空 | 数据保留版本 |
| `AI_PROVIDER` | `deepseek` / `dots` | 不允许生产 `mock` |
| `AI_DEEPSEEK_API_KEY` / `AI_DOTS_API_KEY` | 对应 provider 的 key | 只放 `.env` |
| `SECRET_KEY` | ≥32 位且非 `change-me-in-local-env` | 生产启用 AI 的共用门禁 |

### 1.2 分功能开关

| 功能 | 开关 | 对应条目 |
| --- | --- | --- |
| AI 总开关 | `AI_MASTER_ENABLED` | 全部 |
| 资料画像/用心度/润色 | `AI_PROFILE_ENABLED` | Q24/Q40/Q49 |
| AI 搜索 | `AI_SEARCH_ENABLED` | Q18 |
| 合拍度 | `AI_COMPATIBILITY_SHADOW_ENABLED`、`AI_COMPATIBILITY_DISPLAY_MODE` | Q44 |
| 推荐 | `AI_RECOMMEND_ENABLED` | 首页推荐 |
| 语音 | `AI_VOICE_ENABLED` + 阿里云 NLS key | Q41 |
| AI 军师/红娘 | `AI_ENABLED` + `/ai/advisor` 路由 | Q62/Q78 |
| AI 分身 | `AI_AVATAR_PROVIDER != disabled` + base_url/model | 分身 |

### 1.3 冒烟

```bash
curl -sS -H "Authorization: Bearer <token>" https://<host>/api/v1/ai/capabilities
```

期望返回 7 个能力位：`master/profile/search/compatibility/voice/advisor/avatar`；对应功能关闭时为 `false`，不能把 `false` 当成接口故障。
再逐项调用真实链路（画像生成、搜索解析、润色、军师建议），确认不是 mock 回显；失败时记录 `X-Request-ID` 与服务端日志。

## 2. 短信（Q1）

| 配置 | 要求 |
| --- | --- |
| `SMS_PROVIDER` | 非 `mock`/`disabled`；生产 `mock` 会在启动时直接失败 |
| `SMS_ACCESS_KEY` / `SMS_ACCESS_SECRET` | 供应商凭证，只放 `.env` |
| 签名/模板 | 已审核通过与业务模板一致 |

冒烟：使用**内部测试号码**走一次验证码发送/校验；确认 60 秒间隔、错误码与日志脱敏。不要在生产调用 `POST /auth/test-login`（仅测试环境可用）。

## 3. 人脸 / 实名 / 学历（Q4/Q32）

| 配置 | 要求 |
| --- | --- |
| `FACE_PROVIDER` | `aliyun`（生产不允许 `mock`） |
| `FACE_ACCESS_KEY_ID` / `FACE_ACCESS_KEY_SECRET` / `FACE_SCENE_ID` | 阿里云 CloudAuth 凭证与场景 |
| `IDENTITY_PROVIDER` | `aliyun`（生产不允许 `mock`/`disabled`） |
| `IDENTITY_ACCESS_KEY_ID` / `IDENTITY_ACCESS_KEY_SECRET` | 实名二要素凭证 |
| `EDUCATION_PROVIDER` | 按供应商配置；未配置保持 `disabled` |

冒烟：
1. 未配置 Provider 时 `POST /users/me/face-verify` 必须 fail-closed `503`，不能放行。
2. 配置后用测试账号完成一次人脸比对，确认 `user_auth.face_verified=1`、认证标签出现。
3. 实名二要素失败/超时必须不自动通过；核对 `realname_status` 与失败原因。

## 4. Q51 `request:fail timeout` 定位

请求日志已经包含 `request_id` 与 `duration_ms`：

```text
request_started method=POST path=/api/v1/... client=...
request_completed method=POST path=/api/v1/... status=200 duration_ms=1832.40 client=... content_length=...
X-Request-ID: <id>
```

排查顺序：
1. 让客户端复现，记录 `X-Request-ID` 与时间点。
2. 服务端日志按 `request_id` 检索，确认是否到达后端、耗时在哪一段。
3. 检查 nginx/网关 `proxy_read_timeout`、`client_max_body_size` 与请求体大小。
4. 若后端无记录 → 优先查网关/网络；若有记录且耗时长 → 查慢 SQL/外部 Provider。
5. 不要通过延长超时掩盖慢查询；先定位再调参。

## 5. 不在本次代码范围

| 条目 | 原因 |
| --- | --- |
| Q57 每日抽签 | 产品未立项；后端无表/接口，需新功能 |
| Q69 方案 B（抽取即计数） | 产品未确认「已捡」语义；当前 P1 方案 A 优先 |
| Q72 活动标题/城市覆盖 Banner 文案 | 产品未定案；本次只透出 `title/start_at/end_at` |

## 6. 证据要求

开闸每一步保留：配置项名称（不记录值）、命令原文、完整 stdout/stderr、退出码、`X-Request-ID`、服务端日志摘录。缺少任一要素视为未完成。

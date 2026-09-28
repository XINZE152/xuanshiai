# Xuanshi AI 后端生产启用前收口续作清单

> 记录日期：2026-09-26
> 当前分支：`main`
> 当前 HEAD 基线：`ae487e8401d3d51e102e791fc521bd85b4405ec0`
> 目标：继续完成 AI/语音后端的安全、可靠性、接口契约和发布验收收口。

## 新会话启动约束

- [ ] 先阅读 `PROJECT_RULES.md`、`AGENTS.md`、`docs/DEVELOPMENT.md` 和本清单。
- [ ] 保留工作区全部既有修改，不执行 `git reset --hard`、`git checkout --` 或覆盖用户改动。
- [ ] 只处理 AI、语音、隐私访问控制及其直接依赖；不扩展到支付、普通媒体、腾讯直播或无关重构。
- [ ] AI 默认关闭，生产 AI 仍禁止启用。
- [ ] 不用 Mock、静默 skip、源码断言或伪造 evidence 替代真实 MySQL、Redis、Provider 验收。
- [ ] 任何接口行为变化同步更新对应 API/协议文档。
- [ ] WebSocket 协议单独维护，不把 WebSocket 错列进 OpenAPI `paths`。

## 当前状态

### 已完成

- [x] 完成 AI 架构/API、安全、数据/Worker、测试/部署/观测审查；结论仍为不可生产上线。
- [x] 完成生产 AI fail-closed 配置、`ai_compatibility` 200/202/404/503 契约、Provider 日志脱敏。
- [x] 完成 60 秒一次性 WebSocket ticket；握手前检查 Redis 单次消费、登录 session 和账号状态。
- [x] 完成 300 秒 HMAC 私有 TTS URL、`_ProtectedStorageFiles`、ASR 内存处理。
- [x] 将实时语音共享上下文移至 `app/services/voice/realtime/context.py`。
- [x] 新增独立 CI `ai-unit` job。
- [x] 更新 AI、语音和墨相师实时 WebSocket 文档，使其基本匹配当前 OpenAPI、ticket 和 `moxiang_journey` 协议。
- [x] 音频签名已绑定 `user` 与 `revision` query 参数；私有本地 TTS URL 缺少签发上下文时不得签名。
- [x] 下载时复查用户账号状态和 `privacy_revision`；账号撤权/注销或旧修订 URL 应拒绝读取；数据库状态不可确认时返回 503。
- [x] 普通语音 WS 和墨相师 WS 已在 TTS 输出边界调用 `sign_voice_audio_for_user`。
- [x] 音频访问异常已与 Provider 异常分离：撤权/注销不触发二次 TTS 回退。
- [x] 上一轮定向语音/AI 测试曾通过：`37 passed, 1 warning`；目标 Ruff、Python 编译和 `git diff --check` 曾通过。

### 当前已知阻塞

- [ ] `tests/test_ai_voice_audio_access.py` 当前有语法结构错误，导致最近一次测试收集失败：

```text
IndentationError: expected an indented block after function definition on line 58
```

当前约在第 58—83 行：`test_private_voice_url_rejects_missing_expired_and_tampered_signatures` 的函数体为空，而跨用户回放测试被插入到该函数前面。必须先恢复测试结构。

- [ ] Docker daemon 当前不可用：

```text
error during connect ... open //./pipe/docker_engine: The system cannot find the file specified
```

因此真实 MySQL/Redis 集成环境、Worker、删除传播、retention 和 Provider 沙箱验收尚未完成。

- [ ] 全仓既有失败仍未收口；此前基线为 `2129 passed, 6 failed, 6 skipped, 23 warnings`，全仓 Ruff 有既有错误。不要把这些无关失败混入本轮语音隐私修复。

## 新会话第一步：修复测试文件结构

文件：`tests/test_ai_voice_audio_access.py`

将第 58—83 行整理为以下两个完整测试，不能保留空函数：

```python
def test_private_voice_url_rejects_missing_expired_and_tampered_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed_url(
        monkeypatch,
        "/storage/uploads/voice/tts/example.mp3",
    )
    query = parse_qs(urlsplit(signed).query)

    assert not verify_voice_audio_signature("voice/tts/example.mp3", {}, now=1_001)
    assert not verify_voice_audio_signature(
        "voice/tts/example.mp3", query, now=1_300
    )
    query["signature"] = ["0" * 64]
    assert not verify_voice_audio_signature(
        "voice/tts/example.mp3", query, now=1_001
    )


def test_signed_voice_url_cannot_be_replayed_as_another_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = _signed_url(monkeypatch, user_id=42, privacy_revision=7)
    query = parse_qs(urlsplit(signed).query)
    query["user"] = ["43"]

    assert not verify_voice_audio_signature("tts/example.mp3", query, now=1_001)
```

修复后先执行：

```bash
.venv/Scripts/python.exe -m py_compile tests/test_ai_voice_audio_access.py tests/test_voice_ws.py
.venv/Scripts/python.exe -m ruff check tests/test_ai_voice_audio_access.py tests/test_voice_ws.py
```

同时检查 `tests/test_voice_ws.py` 新增测试和前一个测试之间至少有两个空行；测试中的长行按项目 Ruff 规则处理。

## 继续核对实现

### 音频访问边界

- [ ] 阅读并核对 `app/services/voice/audio_access.py`：
  - [ ] 私有路径仅包括 `tts/` 和 `voice/tts/`。
  - [ ] 外部 Provider URL 原样返回，不进入站内 HMAC 信任边界。
  - [ ] HMAC 覆盖路径、过期时间、用户 ID、隐私修订。
  - [ ] `privacy_revision`、账号状态和签名过期均在下载时复查。
  - [ ] `VoiceAudioDenied` 返回拒绝；`VoiceAudioUnavailable` 不被吞掉。
- [ ] 阅读 `app/main.py:_ProtectedStorageFiles`：
  - [ ] 未签名、篡改、跨用户、旧 revision、过期 URL 返回 `403`。
  - [ ] 数据库故障返回 `503`。
  - [ ] 合法签名且文件存在返回 `200`。
  - [ ] 合法签名但文件已清理返回 `404`。
- [ ] 核对 `app/services/ai/consents.py` 的撤权链路：每类 AI 撤权是否实际递增 `user_revision_state.privacy_revision`，并在事务内完成。
- [ ] 核对 `app/services/user_cancellation_admin.py`：注销批准将 `users.status` 改为非活跃状态，因此旧音频 URL 应立即拒绝；不要依赖签名自然过期。
- [ ] 核对注销取消/重新激活后的隐私修订和历史 URL 行为，避免旧 URL 在重新激活后意外恢复访问。

### 全部音频签发调用点

逐一搜索并确认以下调用点没有重复签名、漏签名或绕过状态复查：

```bash
rg -n "sign_voice_audio_url|sign_voice_audio_for_user|tts_audio_url|audio_url" app tests docs
```

重点文件：

- `app/api/routes/voice.py`
- `app/api/routes/voice_ws.py`
- `app/api/routes/voice_moxiang.py`
- `app/services/voice/gateway.py`
- `app/services/voice/audio_access.py`
- `app/main.py`

检查要求：

- [ ] REST TTS 返回的本地 URL 经过当前用户和隐私修订绑定。
- [ ] 两个 WebSocket 的 `listen` 返回本地 URL 前均经过当前用户状态复查。
- [ ] 流式 TTS 签名失败不会调用整段 TTS 回退。
- [ ] Provider 外部 URL 不被错误地加站内 query 参数。
- [ ] 不把 ASR 伪装成可下载本地文件。

## 定向验证

修复测试结构后运行：

```bash
.venv/Scripts/python.exe -m pytest tests/test_ai_voice_audio_access.py tests/test_voice_ws.py tests/test_voice_providers_aliyun.py -q
```

然后运行完整 AI/语音定向集合：

```bash
.venv/Scripts/python.exe -m pytest \
  tests/test_ai_schema_and_provider.py \
  tests/test_ai_api_contracts.py \
  tests/test_ai_config_hardening.py \
  tests/test_ai_voice_audio_access.py \
  tests/test_voice_auth.py \
  tests/test_voice_ws.py \
  tests/test_voice_providers_aliyun.py \
  tests/test_voice_cleanup.py -q
```

代码质量检查：

```bash
.venv/Scripts/python.exe -m ruff check \
  app/services/voice/audio_access.py \
  app/api/routes/voice.py \
  app/api/routes/voice_ws.py \
  app/api/routes/voice_moxiang.py \
  app/main.py \
  tests/test_ai_voice_audio_access.py \
  tests/test_voice_ws.py

python -m py_compile \
  app/services/voice/audio_access.py \
  app/api/routes/voice.py \
  app/api/routes/voice_ws.py \
  app/api/routes/voice_moxiang.py \
  app/main.py

git diff --check
```

## 文档收口

文件：`docs/api/语音.md`

- [ ] 删除重复的播放说明，只保留一条。
- [ ] 成功响应示例补齐本地 URL 的 `user`、`revision`、`expires`、`signature` 语义，或明确 query 由服务端生成、客户端不得改写。
- [ ] 删除旧的未签名示例 `/storage/voice/tts/...`。
- [ ] 只保留 `/storage/uploads/tts/` 和 `/storage/uploads/voice/tts/` 两类本地 TTS 路径。
- [ ] 删除重复的 `§5` 标题。
- [ ] 补充撤权/注销后旧 URL 立即失效、数据库不可用返回 `503`、文件清理后返回 `404`。
- [ ] 说明 `AI_POLICY_DENIED` 与 `AI_TEMPORARILY_UNAVAILABLE` 的 WebSocket 处理。

文件：`docs/api/墨相师实时整理WebSocket.md`

- [ ] 音频签名说明补充 `user`、`revision` 是服务端绑定的 query 参数，不得删除或替换。
- [ ] 补充撤权、注销、旧 revision 和数据库故障的实际返回行为。
- [ ] 确认无旧 `profile_build` 协议示例被误写成当前协议。
- [ ] 确认没有把 WebSocket 路径写入 OpenAPI HTTP 路径表。

文档审计：

```bash
rg -n "storage/voice|profile_build|token=<access_token>|未签名|匿名|signature|revision|privacy_revision|moxiang_journey" docs/api
```

## 真实依赖验收

Docker daemon 恢复后，先阅读：

- `compose.ai-test.yml`
- `tests/integration/ai/conftest.py`
- `tests/integration/ai/test_ai_privacy_matrix.py`
- `tests/integration/ai/test_ai_deletion_propagation.py`
- `tests/integration/ai/test_ai_cleanup_regrant_race.py`
- `docs/runbooks/ai-retention-and-recovery.md`

执行原则：

- [ ] 使用真实 MySQL 和 Redis；不可改成 fake 或静默 skip。
- [ ] 验证 consent grant/revoke、`privacy_revision` 变化和旧音频 URL 即时失效。
- [ ] 验证注销后音频下载、AI 读面、记忆和派生数据均不可访问。
- [ ] 验证撤权后迟到的 cleanup 不会误删重新授权后的数据。
- [ ] 验证 Worker 任务状态、重试、幂等和 retention 清理。
- [ ] 使用 Provider 沙箱或真实测试凭据验证 ASR/TTS；不要用源码断言替代 Provider 行为。
- [ ] 注明 Docker/Provider 不可用时的准确阻塞，不生成伪造 evidence。

## 发布前仍未完成

- [ ] Provider 沙箱、超时、429、5xx、故障注入验收。
- [ ] MySQL/Redis 真实任务、Worker、consent、删除传播和 retention 验收。
- [ ] 监控指标、告警阈值、disable/rollback 演练。
- [ ] 灰度启用顺序与回滚方案。
- [ ] `scripts/build_ai_evidence.py` 生成真实 evidence。
- [ ] `scripts/verify_ai_release.py` 在真实证据上通过。
- [ ] 全仓既有失败分离归因并单独收口。
- [ ] 在所有发布门禁通过前，保持生产 AI 关闭。

## 验收出口条件

只有以下条件全部满足，才允许讨论生产 AI enablement：

- [ ] 定向单测、Ruff、编译和 `git diff --check` 通过。
- [ ] 真实 MySQL/Redis 集成测试通过，无静默 skip。
- [ ] 真实 Provider 沙箱验收通过，包含失败和超时路径。
- [ ] 撤权、注销、旧 URL、跨用户、DB 故障隐私矩阵通过。
- [ ] 删除传播、retention、重新授权竞态和 Worker 幂等通过。
- [ ] 监控、告警、disable、rollback 和灰度证据齐全。
- [ ] evidence 脚本和发布核验脚本基于真实结果通过。
- [ ] 发布批准顺序已明确；未批准前不得开启生产 AI。

## 新会话推荐首条消息

```text
继续执行 `.pi/plan/ai-backend-production-closeout-followup-20260926.md`。
先修复 `tests/test_ai_voice_audio_access.py` 第 58—83 行的测试结构错误，运行该清单中的语法、Ruff 和定向测试；保留所有既有工作区修改，不启用生产 AI，不用 fake/静默 skip 替代真实验收。测试恢复后继续核对 privacy_revision 撤权链路、注销状态、所有音频签发调用点，并同步语音文档。
```

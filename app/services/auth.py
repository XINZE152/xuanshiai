"""Authentication business rules for SMS, WeChat and session management."""

import asyncio
import hashlib
import json
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import SMS_COMPENSATE_LUA, SMS_ISSUE_LUA, redis_client
from app.core.security import (
    create_token,
    encrypt_sensitive,
    hash_passwordless_code,
    hash_token,
    mask_id_card,
    random_token,
    verify_password,
)
from app.schemas.auth import (
    BindPhoneRequest,
    ChangePhoneRequest,
    PhoneLoginRequest,
    RealNameRequest,
    RefreshRequest,
    WechatLoginRequest,
    ExistingAccountLoginRequest,
)
from app.services.identity_verify.base import IdentityVerifyRequest, IdentityVerifyResult
from app.services.identity_verify.providers import DisabledIdentityProvider, get_identity_provider
from app.services.sms.providers import get_sms_provider
from app.services.user_columns import should_reset_phone_region
from app.services.wechat.providers import get_wechat_provider
from app.services.presence import mark_session_online
from app.services.points import auto_login_reward
from app.services.runtime_config import mini_realname_enabled, platform_registration_open, same_id_multiple_accounts_allowed


logger = logging.getLogger(__name__)


def normalize_user_agent(user_agent: str | None) -> str | None:
    """Keep request metadata within the database column size used by login records."""
    return user_agent[:255] if user_agent else user_agent


class SmsStore:
    """短信验证码存储。

    D-20：以 Redis 为唯一真源（多实例一致、重启不丢），键带 TTL。
    开发/测试环境在 Redis 不可用时退回进程内字典；生产环境 Redis 不可用时
    fail-closed（503），绝不降级为进程内状态。
    """

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._daily: dict[tuple[str, str], tuple[date, int]] = {}
        self._lock = asyncio.Lock()

    # ---- Redis 键 ----
    @staticmethod
    def _code_key(phone: str, purpose: str) -> str:
        return f"sms:code:{phone}:{purpose}"

    @staticmethod
    def _interval_key(phone: str, purpose: str) -> str:
        return f"sms:interval:{phone}:{purpose}"

    @staticmethod
    def _daily_key(phone: str, purpose: str) -> str:
        return f"sms:daily:{phone}:{purpose}:{datetime.now(UTC).date().isoformat()}"

    @staticmethod
    def _fallback_enabled() -> bool:
        return settings.environment in {"development", "testing"}

    @staticmethod
    def _seconds_to_utc_midnight(now: datetime) -> int:
        reset_at = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return max(60, int((reset_at - now).total_seconds()))

    # ---- Redis 实现 ----
    async def _issue_redis(self, phone: str, purpose: str, ip: str, device_id: str | None, code: str) -> str:
        now = datetime.now(UTC)
        interval_key = self._interval_key(phone, purpose)
        daily_key = self._daily_key(phone, purpose)
        payload = json.dumps(
            {
                "hash": hash_passwordless_code(code),
                "sent_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=settings.sms_code_expire_seconds)).isoformat(),
                "attempts": 0,
                "locked_until": None,
                "ip": ip,
                "device_id": device_id,
            },
            ensure_ascii=False,
        )
        result = await redis_client.eval(
            SMS_ISSUE_LUA,
            3,
            interval_key,
            daily_key,
            self._code_key(phone, purpose),
            settings.sms_send_interval_seconds,
            settings.sms_daily_limit,
            self._seconds_to_utc_midnight(now),
            payload,
            settings.sms_code_expire_seconds,
        )
        if int(result) == -1:
            ttl = await redis_client.ttl(interval_key)
            retry = int(ttl) if isinstance(ttl, int) and ttl > 0 else settings.sms_send_interval_seconds
            raise HTTPException(429, detail=f"发送过于频繁，请{max(1, retry)}秒后重试")
        if int(result) == -2:
            raise HTTPException(429, detail="今日验证码发送次数已达上限")
        return payload

    async def _compensate_redis(self, phone: str, purpose: str, payload: str) -> None:
        await redis_client.eval(
            SMS_COMPENSATE_LUA,
            3,
            self._interval_key(phone, purpose),
            self._daily_key(phone, purpose),
            self._code_key(phone, purpose),
            payload,
        )

    async def _rollback_local(self, phone: str, purpose: str) -> None:
        async with self._lock:
            key = (phone, purpose)
            if key in self._items:
                self._items.pop(key, None)
                day, count = self._daily.get(key, (datetime.now(UTC).date(), 0))
                self._daily[key] = (day, max(0, count - 1))

    async def _verify_redis(self, phone: str, purpose: str, code: str) -> None:
        key = self._code_key(phone, purpose)
        raw = await redis_client.get(key)
        if not raw:
            raise HTTPException(400, detail="验证码不存在或已过期")
        item = json.loads(raw)
        now = datetime.now(UTC)
        locked_until = item.get("locked_until")
        if locked_until and datetime.fromisoformat(locked_until) > now:
            raise HTTPException(429, detail="验证码错误次数过多，请稍后重试")
        if datetime.fromisoformat(item["expires_at"]) <= now:
            await redis_client.delete(key)
            raise HTTPException(400, detail="验证码已过期")
        ttl = await redis_client.ttl(key)
        ttl = int(ttl) if isinstance(ttl, int) and ttl > 0 else settings.sms_code_expire_seconds
        if not secrets.compare_digest(item["hash"], hash_passwordless_code(code)):
            item["attempts"] = int(item.get("attempts") or 0) + 1
            if item["attempts"] >= 3:
                item["locked_until"] = (now + timedelta(minutes=15)).isoformat()
                await redis_client.set(key, json.dumps(item, ensure_ascii=False), ex=ttl)
                raise HTTPException(429, detail="验证码错误次数过多，请15分钟后重试")
            await redis_client.set(key, json.dumps(item, ensure_ascii=False), ex=ttl)
            raise HTTPException(400, detail="验证码错误")
        await redis_client.delete(key)

    # ---- 进程内回退（仅 development / testing）----
    async def _issue_local(self, phone: str, purpose: str, ip: str, device_id: str | None, code: str) -> None:
        now = datetime.now(UTC)
        async with self._lock:
            key = (phone, purpose)
            old = self._items.get(key)
            if old and old["sent_at"] + timedelta(seconds=settings.sms_send_interval_seconds) > now:
                retry = int((old["sent_at"] + timedelta(seconds=settings.sms_send_interval_seconds) - now).total_seconds())
                raise HTTPException(429, detail=f"发送过于频繁，请{max(1, retry)}秒后重试")
            count_key = (phone, purpose)
            day, count = self._daily.get(count_key, (now.date(), 0))
            if day != now.date():
                count = 0
            if count >= settings.sms_daily_limit:
                raise HTTPException(429, detail="今日验证码发送次数已达上限")
            self._items[key] = {
                "hash": hash_passwordless_code(code),
                "sent_at": now,
                "expires_at": now + timedelta(seconds=settings.sms_code_expire_seconds),
                "attempts": 0,
                "locked_until": None,
                "ip": ip,
                "device_id": device_id,
            }
            self._daily[count_key] = (now.date(), count + 1)

    async def _verify_local(self, phone: str, purpose: str, code: str) -> None:
        now = datetime.now(UTC)
        async with self._lock:
            item = self._items.get((phone, purpose))
            if not item:
                raise HTTPException(400, detail="验证码不存在或已过期")
            if item["locked_until"] and item["locked_until"] > now:
                raise HTTPException(429, detail="验证码错误次数过多，请稍后重试")
            if item["expires_at"] <= now:
                self._items.pop((phone, purpose), None)
                raise HTTPException(400, detail="验证码已过期")
            if not secrets.compare_digest(item["hash"], hash_passwordless_code(code)):
                item["attempts"] += 1
                if item["attempts"] >= 3:
                    item["locked_until"] = now + timedelta(minutes=15)
                    raise HTTPException(429, detail="验证码错误次数过多，请15分钟后重试")
                raise HTTPException(400, detail="验证码错误")
            self._items.pop((phone, purpose), None)

    async def issue(self, phone: str, purpose: str, ip: str, device_id: str | None) -> int:
        if settings.sms_provider.lower() == "disabled":
            await get_sms_provider().send_code(phone, purpose, "")
        code = settings.sms_mock_code if settings.sms_provider.lower() == "mock" else f"{secrets.randbelow(1_000_000):06d}"
        reserved_payload: str | None = None
        using_local = False
        try:
            reserved_payload = await self._issue_redis(phone, purpose, ip, device_id, code)
        except RedisError as exc:
            if not self._fallback_enabled():
                raise HTTPException(503, detail="Redis服务未配置或暂时不可用") from exc
            await self._issue_local(phone, purpose, ip, device_id, code)
            using_local = True
        try:
            await get_sms_provider().send_code(phone, purpose, code)
        except Exception:
            try:
                if using_local:
                    await self._rollback_local(phone, purpose)
                elif reserved_payload is not None:
                    await self._compensate_redis(phone, purpose, reserved_payload)
            except RedisError:
                pass
            raise
        return settings.sms_code_expire_seconds

    async def verify(self, phone: str, purpose: str, code: str) -> None:
        try:
            await self._verify_redis(phone, purpose, code)
        except RedisError as exc:
            if not self._fallback_enabled():
                raise HTTPException(503, detail="Redis服务未配置或暂时不可用") from exc
            await self._verify_local(phone, purpose, code)


sms_store = SmsStore()


async def exchange_wechat_code(code: str) -> dict[str, str]:
    if settings.wechat_provider.lower() == "mock":
        return await get_wechat_provider().exchange_code(code)
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(503, detail="微信登录服务未配置")
    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": settings.wechat_app_id,
        "secret": settings.wechat_app_secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, detail="微信登录服务暂时不可用") from exc
    if data.get("errcode") or not data.get("openid"):
        raise HTTPException(400, detail="微信登录凭证无效")
    return {key: data[key] for key in ("openid", "unionid") if key in data}


async def create_session(
    db: AsyncSession,
    user_id: int,
    device_id: str | None,
    platform: str | None,
    app_version: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    await db.execute(
        text(
            """UPDATE user_session SET status = 2, revoked_at = UTC_TIMESTAMP(), revoke_reason = 'session_limit'
               WHERE user_id = :user_id AND status = 1
                 AND id NOT IN (SELECT id FROM (SELECT id FROM user_session WHERE user_id = :user_id
                   AND status = 1 ORDER BY created_at DESC LIMIT :keep) recent)"""
        ),
        {"user_id": user_id, "keep": max(0, settings.max_sessions_per_user - 1)},
    )
    refresh = random_token()
    now = datetime.now(UTC).replace(tzinfo=None)
    access_expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    refresh_expire = now + timedelta(days=settings.refresh_token_expire_days)
    result = await db.execute(
        text(
            """INSERT INTO user_session
               (user_id, refresh_token_hash, device_id, platform, app_version, ip, user_agent,
                created_at, last_used_at, access_expire_at, refresh_expire_at, status)
               VALUES (:user_id, :refresh_hash, :device_id, :platform, :app_version, :ip, :ua,
                       UTC_TIMESTAMP(), UTC_TIMESTAMP(), :access_expire, :refresh_expire, 1)"""
        ),
        {"user_id": user_id, "refresh_hash": hash_token(refresh), "device_id": device_id,
         "platform": platform, "app_version": app_version, "ip": ip, "ua": user_agent,
         "access_expire": access_expire, "refresh_expire": refresh_expire},
    )
    session_id = result.lastrowid
    access = create_token(user_id, session_id, "access", timedelta(minutes=settings.access_token_expire_minutes))
    await db.execute(
        text("UPDATE user_session SET access_token_hash = :hash WHERE id = :id"),
        {"hash": hash_token(access), "id": session_id},
    )
    await mark_session_online(db, user_id, int(session_id))
    await auto_login_reward(db, user_id)
    return {"access_token": access, "refresh_token": refresh, "expires_in": settings.access_token_expire_minutes * 60,
            "user_id": user_id}


# users.source（会员登记来源：后台添加/红娘录入/批量导入/自己注册/落地页/父母登记/线索入库/…）
# 属 20261011 补列项。注册是核心入口，迁移未执行的环境探测不到该列时静默不写，
# 不能让一句 Unknown column 把新用户挡在门外。
SOURCE_SELF_REGISTER = "自己注册"


async def _users_columns(db: AsyncSession) -> frozenset[str]:
    rows = await db.execute(text("""SELECT COLUMN_NAME FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'"""))
    return frozenset(row[0] for row in rows.all())


def _render_user_insert(columns: list[str], values: list[str]) -> str:
    """渲染 users 表的 INSERT：取值位与列必须一一对应，且每项自带冒号（或是 SQL 字面量）。

    长度断言防的是 2026-10-31 客源录入事故：把冒号写在 ``', '.join()`` 外面只会给首列
    加占位符，其余列退化成同表裸列名引用，MySQL 不报错但写入 NULL/列默认值。
    """
    if len(columns) != len(values):
        raise ValueError(f"users INSERT 列数({len(columns)})与取值位数({len(values)})不一致")
    return f"INSERT INTO users ({', '.join(columns)}) VALUES ({', '.join(values)})"


async def get_or_create_user_by_phone(db: AsyncSession, phone: str, ip: str | None) -> int:
    result = await db.execute(text("SELECT id, status FROM users WHERE phone = :phone FOR UPDATE"), {"phone": phone})
    row = result.mappings().first()
    if row:
        if row["status"] != 1:
            raise HTTPException(403, detail="账号当前不可用")
        user_id = row["id"]
        await ensure_default_user_role(db, user_id)
        return user_id
    if not await platform_registration_open(db):
        raise HTTPException(403, detail="平台当前暂停新用户注册，请联系红娘开通后再试")
    # 取值位逐项自带冒号（或写成 SQL 字面量）：把冒号留在 join 外面只会给首列加占位符，
    # 其余列退化成裸列名引用，MySQL 不报错但写入的是 NULL/列默认值。
    columns = ["phone", "phone_verified_at", "register_ip"]
    values = [":phone", "UTC_TIMESTAMP()", ":ip"]
    payload: dict[str, Any] = {"phone": phone, "ip": ip}
    if "source" in await _users_columns(db):
        columns.append("source")
        values.append(":source")
        payload["source"] = SOURCE_SELF_REGISTER
    result = await db.execute(
        text(_render_user_insert(columns, values)),
        payload,
    )
    user_id = result.lastrowid
    await ensure_default_user_role(db, user_id)
    return user_id


async def ensure_default_user_role(db: AsyncSession, user_id: int) -> None:
    """为历史或新建用户补齐普通用户角色，避免角色查询为空。"""
    await db.execute(text("""INSERT INTO user_role (user_id, role_code, status)
        VALUES (:user_id, 'user', 1)
        ON DUPLICATE KEY UPDATE status = 1"""), {"user_id": user_id})


async def touch_login(db: AsyncSession, user_id: int, extra_set: str = "") -> None:
    """登录时刷新 last_login_at，并在 users.login_num 列已迁移时自增登录次数（A12）。

    login_num 为补列项：迁移未执行的环境（information_schema 查不到该列）静默跳过自增，
    不抛 Unknown column。登录为写路径，这里一次轻量探测保证跨环境兼容。
    """
    has_login_num = (await db.execute(text("""SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'login_num'"""))).first()
    set_clause = "last_login_at = UTC_TIMESTAMP()"
    if extra_set:
        set_clause = f"{extra_set}, {set_clause}"
    if has_login_num:
        set_clause = f"login_num = login_num + 1, {set_clause}"
    await db.execute(text(f"UPDATE users SET {set_clause} WHERE id = :id"), {"id": user_id})


async def login_phone(db: AsyncSession, request: PhoneLoginRequest, ip: str | None, user_agent: str | None) -> dict[str, Any]:
    if request.purpose != "login":
        raise HTTPException(422, detail="手机号登录验证码用途必须为login")
    await sms_store.verify(request.phone, request.purpose, request.code)
    user_agent = normalize_user_agent(user_agent)
    async with db.begin():
        user_id = await get_or_create_user_by_phone(db, request.phone, ip)
        await touch_login(db, user_id)
        tokens = await create_session(db, user_id, request.device_id, request.platform, request.app_version, ip, user_agent)
        await db.execute(text("INSERT INTO user_login_log (user_id, login_type, ip, device) VALUES (:id, 2, :ip, :device)"), {"id": user_id, "ip": ip, "device": user_agent})
    tokens["need_bind_phone"] = False
    return tokens


async def login_wechat(db: AsyncSession, request: WechatLoginRequest, ip: str | None, user_agent: str | None) -> dict[str, Any]:
    identity = await exchange_wechat_code(request.code)
    user_agent = normalize_user_agent(user_agent)
    async with db.begin():
        result = await db.execute(text("SELECT id, phone, status FROM users WHERE openid = :openid FOR UPDATE"), {"openid": identity["openid"]})
        row = result.mappings().first()
        if row and row["status"] != 1:
            raise HTTPException(403, detail="账号当前不可用")
        if row:
            user_id = row["id"]
            await touch_login(db, user_id, extra_set="nickname = COALESCE(:nickname, nickname), avatar = COALESCE(:avatar, avatar)")
            phone = row["phone"]
        else:
            if not await platform_registration_open(db):
                raise HTTPException(403, detail="平台当前暂停新用户注册，请联系红娘开通后再试")
            columns = ["openid", "unionid", "nickname", "avatar", "register_ip"]
            values = [":openid", ":unionid", ":nickname", ":avatar", ":ip"]
            payload: dict[str, Any] = {
                "openid": identity["openid"], "unionid": identity.get("unionid"),
                "nickname": request.nickname, "avatar": request.avatar, "ip": ip,
            }
            if "source" in await _users_columns(db):
                columns.append("source")
                values.append(":source")
                payload["source"] = SOURCE_SELF_REGISTER
            result = await db.execute(
                text(_render_user_insert(columns, values)),
                payload,
            )
            user_id, phone = result.lastrowid, None
        await ensure_default_user_role(db, user_id)
        tokens = await create_session(db, user_id, request.device_id, request.platform, request.app_version, ip, user_agent)
        await db.execute(text("INSERT INTO user_login_log (user_id, login_type, ip, device) VALUES (:id, 1, :ip, :device)"), {"id": user_id, "ip": ip, "device": user_agent})
    tokens["need_bind_phone"] = not bool(phone)
    return tokens


async def login_existing_account(db: AsyncSession, request: ExistingAccountLoginRequest, ip: str | None, user_agent: str | None) -> dict[str, Any]:
    """Issue a normal session for an existing account without SMS/WeChat."""
    if not settings.is_test_mode:
        raise HTTPException(403, detail="已有账号测试登录仅允许在 development/testing 环境使用")
    user_agent = normalize_user_agent(user_agent)
    async with db.begin():
        result = await db.execute(text("SELECT id, phone, password, status FROM users WHERE phone = :phone FOR UPDATE"), {"phone": request.phone})
        row = result.mappings().first()
        if not row:
            raise HTTPException(404, detail="账号不存在")
        if int(row["status"]) != 1:
            raise HTTPException(403, detail="账号当前不可用")
        if not row["password"] or not verify_password(request.password, row["password"]):
            raise HTTPException(401, detail="手机号或密码错误")
        await ensure_default_user_role(db, int(row["id"]))
        await touch_login(db, int(row["id"]))
        tokens = await create_session(db, int(row["id"]), request.device_id, request.platform, request.app_version, ip, user_agent)
        await db.execute(text("INSERT INTO user_login_log (user_id, login_type, ip, device) VALUES (:id, 3, :ip, :device)"), {"id": row["id"], "ip": ip, "device": user_agent})
    tokens["need_bind_phone"] = not bool(row["phone"])
    return tokens


async def bind_phone(db: AsyncSession, user_id: int, request: BindPhoneRequest, ip: str | None) -> None:
    if request.purpose != "bind_phone":
        raise HTTPException(422, detail="绑定手机号验证码用途必须为bind_phone")
    bound = await db.execute(text("SELECT phone FROM users WHERE id = :id"), {"id": user_id})
    bound_phone = bound.scalar()
    if bound_phone and bound_phone != request.phone:
        # 此前该接口对已绑定账号直接覆盖 phone，只校验新号验证码：任何持有有效
        # token 的人都能用自己的号码改绑并接管账号。已绑定用户必须走换绑流程。
        raise HTTPException(409, detail="账号已绑定手机号，请使用换绑流程")
    await sms_store.verify(request.phone, "bind_phone", request.code)
    async with db.begin():
        result = await db.execute(text("SELECT id, phone FROM users WHERE id = :id FOR UPDATE"), {"id": user_id})
        current = result.mappings().first()
        if not current:
            raise HTTPException(404, detail="用户不存在")
        if current["phone"] == request.phone:
            return
        if current["phone"]:
            raise HTTPException(409, detail="账号已绑定手机号，请使用换绑流程")
        result = await db.execute(text("SELECT id FROM users WHERE phone = :phone FOR UPDATE"), {"phone": request.phone})
        if result.first():
            raise HTTPException(409, detail="该手机号已绑定其他账号")
        set_parts = [
            "phone = :phone",
            "phone_verified_at = UTC_TIMESTAMP()",
            "updated_at = UTC_TIMESTAMP()",
        ]
        # 换号后旧号码的归属地必须失效，否则列表会一直显示旧号属地。
        if await should_reset_phone_region(db, phone_changed=True):
            set_parts.append("phone_region = NULL")
        await db.execute(
            text(f"UPDATE users SET {', '.join(set_parts)} WHERE id = :id"),
            {"phone": request.phone, "id": user_id},
        )


async def change_phone(
    db: AsyncSession,
    user_id: int,
    session_id: int,
    request: ChangePhoneRequest,
    ip: str | None,
) -> None:
    """换绑手机号：旧号（验证码或密码）+ 新号验证码双证明，成功后踢掉其他会话。

    与 ``bind_phone`` 的区别：bind 只服务「从未绑定」的账号（微信登录后补绑），
    因此对已绑定账号直接 409；换绑必须在这里完成，且两端都要证明归属。
    """
    async with db.begin():
        result = await db.execute(
            text("SELECT id, phone, password FROM users WHERE id = :id FOR UPDATE"), {"id": user_id}
        )
        row = result.mappings().first()
        if not row:
            raise HTTPException(404, detail="用户不存在")
        current_phone = row["phone"]
        if not current_phone:
            raise HTTPException(409, detail="账号尚未绑定手机号，请使用绑定流程")
        if current_phone == request.new_phone:
            raise HTTPException(422, detail="新手机号与当前手机号相同")
        if request.old_code:
            await sms_store.verify(current_phone, "change_phone_old", request.old_code)
        else:
            if not row["password"] or not verify_password(request.password or "", row["password"]):
                raise HTTPException(401, detail="账号密码错误")
        await sms_store.verify(request.new_phone, "change_phone_new", request.new_code)
        occupied = await db.execute(
            text("SELECT id FROM users WHERE phone = :phone FOR UPDATE"), {"phone": request.new_phone}
        )
        if occupied.first():
            raise HTTPException(409, detail="该手机号已绑定其他账号")
        set_parts = [
            "phone = :phone",
            "phone_verified_at = UTC_TIMESTAMP()",
            "updated_at = UTC_TIMESTAMP()",
        ]
        # 换绑后旧号码的归属地必须失效；不在此处同步解析新号，保持换绑热路径纯净。
        if await should_reset_phone_region(db, phone_changed=True):
            set_parts.append("phone_region = NULL")
        await db.execute(
            text(f"UPDATE users SET {', '.join(set_parts)} WHERE id = :id"),
            {"phone": request.new_phone, "id": user_id},
        )
        # 换绑等同凭证变更：除当前设备外全部会话失效，防止旧号留存设备继续使用。
        await db.execute(
            text(
                "UPDATE user_session SET status = 2, revoked_at = UTC_TIMESTAMP(), revoke_reason = 'phone_changed'"
                " WHERE user_id = :id AND status = 1 AND id <> :sid"
            ),
            {"id": user_id, "sid": session_id},
        )


async def refresh_session(db: AsyncSession, request: RefreshRequest, ip: str | None, user_agent: str | None) -> dict[str, Any]:
    disabled_user_id: int | None = None
    async with db.begin():
        result = await db.execute(text("""SELECT s.id, s.user_id, s.device_id, s.platform, s.app_version,
            s.refresh_expire_at, u.status AS user_status
            FROM user_session s JOIN users u ON u.id = s.user_id
            WHERE s.refresh_token_hash = :hash AND s.status = 1 FOR UPDATE"""), {"hash": hash_token(request.refresh_token)})
        row = result.mappings().first()
        if not row or row["refresh_expire_at"] <= datetime.now(UTC).replace(tzinfo=None):
            raise HTTPException(401, detail="刷新令牌无效或已过期")
        # D-19：被封禁 / 停用账号不得通过刷新令牌续命；同时失效其全部在线会话。
        if int(row["user_status"]) != 1:
            disabled_user_id = int(row["user_id"])
            await db.execute(text("""UPDATE user_session SET status = 2, revoked_at = UTC_TIMESTAMP(),
                revoke_reason = 'user_disabled' WHERE user_id = :uid AND status = 1"""), {"uid": disabled_user_id})
        else:
            await db.execute(text("UPDATE user_session SET status = 2, revoked_at = UTC_TIMESTAMP(), revoke_reason = 'rotated' WHERE id = :id"), {"id": row["id"]})
            tokens = await create_session(db, row["user_id"], row["device_id"], row["platform"], row["app_version"], ip, user_agent)
    if disabled_user_id is not None:
        raise HTTPException(403, detail="账号状态异常，请重新登录")
    result = await db.execute(text("SELECT phone IS NOT NULL AS has_phone FROM users WHERE id = :id"), {"id": row["user_id"]})
    tokens["need_bind_phone"] = not bool(result.scalar())
    return tokens


async def revoke_session(db: AsyncSession, session_id: int) -> None:
    await db.execute(text("UPDATE user_session SET status = 2, revoked_at = UTC_TIMESTAMP(), revoke_reason = 'logout' WHERE id = :id AND status = 1"), {"id": session_id})


# A12-模拟登录：受限短时只读会话。默认 15 分钟过期，token 带 imp 声明，
# 由 app/main.py 只读中间件拒绝写操作。不触发登录奖励/在线标记等普通登录副作用。
IMPERSONATION_TOKEN_EXPIRE_MINUTES = 15


async def create_impersonation_session(
    db: AsyncSession,
    admin_id: int,
    user_id: int,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    """为后台管理员签发一个面向目标用户的短时只读模拟会话，返回仅含 access_token。

    安全收口：目标账号必须存在且 status=1；会话带 impersonated_by（缺列时降级不写，
    仍由 token 声明保证只读）；审计写入 business_audit_log（admin.impersonate.create）。
    """
    user = (await db.execute(
        text("SELECT id, status FROM users WHERE id = :id"), {"id": user_id}
    )).mappings().first()
    if not user:
        raise HTTPException(404, detail="目标用户不存在")
    if int(user["status"]) != 1:
        raise HTTPException(409, detail="目标账号当前不可用，无法模拟登录")
    has_col = (await db.execute(text(
        """SELECT 1 FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_session' AND COLUMN_NAME = 'impersonated_by'"""
    ))).first()
    minutes = getattr(settings, "impersonation_token_expire_minutes", None) or IMPERSONATION_TOKEN_EXPIRE_MINUTES
    refresh = random_token()
    now = datetime.now(UTC).replace(tzinfo=None)
    access_expire = now + timedelta(minutes=minutes)
    params: dict[str, Any] = {
        "user_id": user_id,
        "refresh_hash": hash_token(refresh),
        "device_id": f"impersonate:{admin_id}",
        "ip": ip,
        "ua": normalize_user_agent(user_agent),
        "access_expire": access_expire,
        "refresh_expire": access_expire,
    }
    if has_col:
        params["imp_by"] = admin_id
        insert_result = await db.execute(
            text(
                """INSERT INTO user_session
                   (user_id, refresh_token_hash, device_id, platform, ip, user_agent,
                    created_at, last_used_at, access_expire_at, refresh_expire_at, status, impersonated_by)
                   VALUES (:user_id, :refresh_hash, :device_id, 'impersonation', :ip, :ua,
                           UTC_TIMESTAMP(), UTC_TIMESTAMP(), :access_expire, :refresh_expire, 1, :imp_by)"""
            ),
            params,
        )
    else:
        insert_result = await db.execute(
            text(
                """INSERT INTO user_session
                   (user_id, refresh_token_hash, device_id, platform, ip, user_agent,
                    created_at, last_used_at, access_expire_at, refresh_expire_at, status)
                   VALUES (:user_id, :refresh_hash, :device_id, 'impersonation', :ip, :ua,
                           UTC_TIMESTAMP(), UTC_TIMESTAMP(), :access_expire, :refresh_expire, 1)"""
            ),
            params,
        )
    session_id = int(insert_result.lastrowid)
    access = create_token(
        user_id, session_id, "access", timedelta(minutes=minutes), extra={"imp": admin_id}
    )
    await db.execute(
        text("UPDATE user_session SET access_token_hash = :hash WHERE id = :id"),
        {"hash": hash_token(access), "id": session_id},
    )
    await db.execute(
        text(
            """INSERT INTO business_audit_log
               (actor_user_id, action, resource_type, resource_id, reason, after_json)
               VALUES (:admin_id, 'admin.impersonate.create', 'user', :user_id, :reason, :after_json)"""
        ),
        {
            "admin_id": admin_id,
            "user_id": user_id,
            "reason": "后台模拟登录",
            "after_json": json.dumps(
                {"session_id": session_id, "expires_in_minutes": minutes, "read_only": True},
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": minutes * 60,
        "impersonated_user_id": user_id,
        "session_id": session_id,
        "read_only": True,
        "impersonated_by": admin_id,
    }


async def accept_agreement(db: AsyncSession, user_id: int, agreement_type: str, version: str, content_hash: str | None, scene: str | None, ip: str | None, device_id: str | None) -> None:
    current = settings.agreement_versions.get(agreement_type)
    if not current or version != current:
        raise HTTPException(409, detail="协议版本不是当前发布版本")
    await db.execute(text("INSERT INTO user_agreement_acceptance (user_id, agreement_type, agreement_version, content_hash, accepted_ip, device_id, scene) VALUES (:uid, :type, :version, :content_hash, :ip, :device, :scene) ON DUPLICATE KEY UPDATE accepted_at = UTC_TIMESTAMP(), accepted_ip = VALUES(accepted_ip), device_id = VALUES(device_id), scene = VALUES(scene), status = 1"), {"uid": user_id, "type": agreement_type, "version": version, "content_hash": content_hash, "ip": ip, "device": device_id, "scene": scene})


def calculate_age(birthday: date) -> int:
    today = date.today()
    return today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))


async def _check_realname_submission_guard(
    db: AsyncSession, *, user_id: int, card_hash: str, allow_same_id: bool
) -> None:
    """校验重复证件与已认证状态；调用方必须处于事务内。"""
    if not allow_same_id:
        duplicate = await db.execute(
            text(
                "SELECT user_id FROM user_auth WHERE id_card_hash = :hash "
                "AND user_id <> :uid AND realname_status = 2"
            ),
            {"hash": card_hash, "uid": user_id},
        )
        if duplicate.first():
            raise HTTPException(409, detail="该身份信息已被其他账号认证")
    current = await db.execute(
        text("SELECT realname_status FROM user_auth WHERE user_id = :uid FOR UPDATE"),
        {"uid": user_id},
    )
    if current.scalar() == 2:
        raise HTTPException(409, detail="实名认证通过后不能修改认证信息")


async def _write_realname_submission(
    db: AsyncSession,
    *,
    user_id: int,
    request: RealNameRequest,
    card_hash: str,
    birth: date,
    allow_same_id: bool,
    status: int,
    reason: str | None,
) -> None:
    """写入一次实名提交；status 1=人工审核 2=自动通过 3=自动未通过。"""
    await _check_realname_submission_guard(
        db, user_id=user_id, card_hash=card_hash, allow_same_id=allow_same_id
    )
    step = 3 if status == 1 else 4 if status == 2 else 5
    await db.execute(
        text(
            """INSERT INTO user_auth
            (user_id, real_name, id_card, id_card_hash, id_card_masked, realname_status,
             auth_status, auth_step, submitted_at, retry_count, fail_reason)
            VALUES (:uid, :name, :card, :hash, :masked, :status, :status, :step,
                    UTC_TIMESTAMP(), 1, :reason)
            ON DUPLICATE KEY UPDATE real_name = VALUES(real_name), id_card = VALUES(id_card),
            id_card_hash = VALUES(id_card_hash), id_card_masked = VALUES(id_card_masked),
            realname_status = :status, auth_status = :status, auth_step = :step,
            submitted_at = UTC_TIMESTAMP(), retry_count = retry_count + 1,
            fail_reason = :reason"""
        ),
        {
            "uid": user_id,
            "name": request.real_name,
            "card": encrypt_sensitive(request.id_card),
            "hash": card_hash,
            "masked": mask_id_card(request.id_card),
            "status": status,
            "step": step,
            "reason": reason,
        },
    )
    reviewed_at = "NULL" if status == 1 else "UTC_TIMESTAMP()"
    await db.execute(
        text(f"UPDATE user_auth SET reviewed_by = NULL, reviewed_at = {reviewed_at} WHERE user_id = :uid"),
        {"uid": user_id},
    )
    await db.execute(
        text("UPDATE users SET birthday = COALESCE(birthday, :birthday) WHERE id = :uid"),
        {"uid": user_id, "birthday": birth},
    )


def _realname_status_for_result(result: IdentityVerifyResult | None) -> tuple[int, str | None]:
    if result is None:
        return 1, None
    if result.verified:
        return 2, None
    return 3, (result.reason or "身份信息核验未通过").strip()[:255]


async def _verify_realname_with_provider(
    user_id: int, request: RealNameRequest
) -> IdentityVerifyResult | None:
    """调用在线核验；未配置/不可用时返回 None，由调用方转人工审核。"""
    provider = get_identity_provider()
    if isinstance(provider, DisabledIdentityProvider):
        return None
    try:
        return await provider.verify(
            IdentityVerifyRequest(
                user_id=user_id,
                real_name=request.real_name.strip(),
                id_card_number=request.id_card.upper(),
            )
        )
    except HTTPException as exc:
        logger.warning(
            "realname_provider_unavailable provider=%s status=%s",
            getattr(provider, "name", "unknown"),
            exc.status_code,
        )
        return None
    except Exception as exc:  # 供应商异常必须降级人工，不能阻断提交
        logger.warning(
            "realname_provider_failed provider=%s error_type=%s",
            getattr(provider, "name", "unknown"),
            type(exc).__name__,
        )
        return None


async def submit_realname(db: AsyncSession, user_id: int, request: RealNameRequest) -> dict[str, Any]:
    if not await mini_realname_enabled(db):
        raise HTTPException(403, detail="实名认证功能暂未开放")
    birth = date(int(request.id_card[6:10]), int(request.id_card[10:12]), int(request.id_card[12:14]))
    if calculate_age(birth) < 18:
        raise HTTPException(422, detail="仅支持年满18周岁的用户认证")
    card_hash = hashlib.sha256(request.id_card.upper().encode()).hexdigest()
    allow_same_id = await same_id_multiple_accounts_allowed(db)
    # Runtime-config reads start a SQLAlchemy transaction on this request.
    # Close it before opening the atomic identity-submission transaction.
    await db.rollback()

    provider = get_identity_provider()
    if isinstance(provider, DisabledIdentityProvider):
        async with db.begin():
            await _write_realname_submission(
                db,
                user_id=user_id,
                request=request,
                card_hash=card_hash,
                birth=birth,
                allow_same_id=allow_same_id,
                status=1,
                reason=None,
            )
        return {"status": 1, "id_card_masked": mask_id_card(request.id_card)}

    # 第三方调用不能放在数据库事务内：先做一次只读校验，调用后再原子写入。
    async with db.begin():
        await _check_realname_submission_guard(
            db, user_id=user_id, card_hash=card_hash, allow_same_id=allow_same_id
        )
    result = await _verify_realname_with_provider(user_id, request)
    status, reason = _realname_status_for_result(result)
    async with db.begin():
        await _write_realname_submission(
            db,
            user_id=user_id,
            request=request,
            card_hash=card_hash,
            birth=birth,
            allow_same_id=allow_same_id,
            status=status,
            reason=reason,
        )
    return {"status": status, "id_card_masked": mask_id_card(request.id_card)}


async def list_realname_reviews(db: AsyncSession, *, page: int, page_size: int, status: int | None = None, search: str | None = None, scope: str | None = None, scope_params: dict[str, object] | None = None):
    from app.schemas.admin import RealnameReviewItem, RealnameReviewPage
    where = "1=1"
    params: dict[str, object] = dict(scope_params or {})
    if scope:
        where += f" AND ({scope})"
    if status is not None:
        where += " AND ua.realname_status = :status"
        params["status"] = status
    if search:
        where += " AND (u.nickname LIKE CONCAT('%', :search, '%') OR u.phone LIKE CONCAT('%', :search, '%'))"
        params["search"] = search
    rows = (await db.execute(text(f"""SELECT ua.user_id, u.nickname, ua.real_name, ua.id_card_masked,
        ua.realname_status, ua.submitted_at, ua.reviewed_at, ua.fail_reason
        FROM user_auth ua JOIN users u ON u.id = ua.user_id
        WHERE {where} ORDER BY ua.submitted_at ASC, ua.user_id ASC"""), params)).mappings().all()
    total = len(rows)
    offset = (page - 1) * page_size
    return RealnameReviewPage(items=[RealnameReviewItem(**dict(row)) for row in rows[offset:offset + page_size]], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


REALNAME_STATUS_PASSED = 2
REALNAME_STATUS_FAILED = 3
REALNAME_STATUS_MANUAL = 4

# 审核通过 / 未通过时人脸标记的取值（face_verified：1 通过 2 未通过 0 未核验）。
_FACE_VERIFIED_BY_STATUS = {
    REALNAME_STATUS_PASSED: 1,
    REALNAME_STATUS_FAILED: 2,
    REALNAME_STATUS_MANUAL: 0,
}


async def apply_realname_review(
    db: AsyncSession,
    user_id: int,
    status: int,
    reason: str | None,
    admin_id: int,
    *,
    sync_face: bool = False,
) -> None:
    """实名审核统一落列（D-14）。

    `realname_status` 是 C 端门禁的**唯一读取列**（`dependencies.py` 要求 ==2）；
    `face_verified` 是人脸核验标记（`get_face_verified_user` 要求 ==1）。
    两个后台入口必须经此函数写入 realname_status，避免「在会员认证后台通过实名
    却不满足 C 端门禁」的双写分裂。

    ``sync_face=True`` 时同步写入人脸标记：仅「会员认证后台」使用，因为它的审核
    记录携带人脸照片与评分（face_photo / face_score），审核即等价于人脸核验；
    纯实名审核入口保持只写 realname_status，不越权授予发帖所需的人脸标记。

    调用方负责前置校验与事务提交。
    """
    if status not in _FACE_VERIFIED_BY_STATUS:
        raise HTTPException(422, detail="不支持的实名认证审核状态")
    assignments = [
        "realname_status=:status",
        "auth_status=:status",
        "auth_step=:auth_step",
        "fail_reason=:reason",
        "reviewed_by=:admin_id",
        "reviewed_at=UTC_TIMESTAMP()",
        "updated_at=UTC_TIMESTAMP()",
    ]
    params: dict[str, Any] = {
        "status": status,
        "auth_step": 4 if status == REALNAME_STATUS_PASSED else 5 if status == REALNAME_STATUS_FAILED else 3,
        "reason": reason,
        "admin_id": admin_id,
        "user_id": user_id,
    }
    if sync_face:
        assignments.append("face_verified=:face")
        params["face"] = _FACE_VERIFIED_BY_STATUS[status]
    await db.execute(
        text(f"UPDATE user_auth SET {', '.join(assignments)} WHERE user_id=:user_id"), params
    )


async def review_realname(db: AsyncSession, user_id: int, status: int, reason: str | None, admin_id: int):
    from app.schemas.admin import RealnameReviewResponse
    if status not in (2, 3, 4):
        raise HTTPException(422, detail="不支持的实名认证审核状态")
    row = (await db.execute(text("SELECT realname_status FROM user_auth WHERE user_id=:user_id FOR UPDATE"), {"user_id": user_id})).mappings().first()
    if not row:
        raise HTTPException(404, detail="实名认证记录不存在")
    if int(row["realname_status"]) != 1:
        raise HTTPException(409, detail="当前实名认证不在审核中")
    await apply_realname_review(db, user_id, status, reason, admin_id)
    await db.commit()
    return RealnameReviewResponse(user_id=user_id, realname_status=status, reason=reason)



"""Common request dependencies and authenticated-user guards."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.services.matchmaker_admin_auth import decode_matchmaker_admin_token, get_session_account
from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)


async def require_sensitive_operation(
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        description="客户端生成的幂等键，同键同载荷只会执行一次",
    ),
    confirm: bool = Query(
        False,
        description="敏感操作二次确认；必须显式传 true，否则返回 428",
    ),
) -> str:
    """敏感后台操作的前置校验：强制幂等键 + 二次确认。

    用于退款、提现审核、重置密码、资源调整等不可逆或涉及金额的操作。
    """
    if not confirm:
        raise HTTPException(
            status_code=428,
            detail="敏感操作需要二次确认：请携带 confirm=true",
        )
    return idempotency_key


@dataclass(frozen=True)
class CurrentUser:
    id: int
    session_id: int
    phone: str | None
    status: int
    realname_status: int
    face_verified: int | None = None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
        session_id = int(payload["sid"])
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="无效或已过期的访问令牌") from exc

    result = await db.execute(
        text(
            """SELECT u.id, u.phone, u.status, COALESCE(ua.realname_status, 0) AS realname_status,
                      COALESCE(ua.face_verified, 0) AS face_verified
               FROM users u LEFT JOIN user_auth ua ON ua.user_id = u.id
               JOIN user_session s ON s.user_id = u.id
               WHERE u.id = :user_id AND s.id = :session_id AND s.status = 1
                 AND s.revoked_at IS NULL AND s.access_expire_at > UTC_TIMESTAMP()"""
        ),
        {"user_id": user_id, "session_id": session_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="登录状态已失效")
    if int(row["status"]) != 1:
        raise HTTPException(status_code=403, detail="账号当前不可用")
    await db.execute(
        text("UPDATE user_session SET last_used_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"id": session_id},
    )
    await db.commit()
    values = dict(row)
    values.setdefault("face_verified", None)
    return CurrentUser(**values, session_id=session_id)


async def get_verified_user(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Require a verified phone before social discovery and interaction actions."""
    if not current.phone:
        raise HTTPException(status_code=403, detail="请先绑定手机号")
    return current


async def get_realname_verified_user(
    current: CurrentUser = Depends(get_verified_user),
) -> CurrentUser:
    """Require realname verification for regular interactions and applications.

    Face verification is intentionally not required here: publishing community
    posts uses :func:`get_face_verified_user` instead. See PRODUCT.md §互动门槛.
    """
    if current.realname_status != 2:
        raise HTTPException(status_code=403, detail="请先完成实名认证")
    return current


async def get_face_verified_user(
    current: CurrentUser = Depends(get_verified_user),
) -> CurrentUser:
    """Require realname plus face verification for publishing community posts.

    Fail-closed: a missing face record is treated as not verified.
    """
    if current.realname_status != 2:
        raise HTTPException(status_code=403, detail="请先完成实名认证")
    if current.face_verified != 1:
        raise HTTPException(status_code=403, detail="请先完成人脸认证")
    return current


async def get_browsable_user(
    current: CurrentUser = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Require the single server-side gate shared by homepage discovery APIs."""
    result = await db.execute(
        text("SELECT COALESCE(score, 0) FROM user_profile_completion WHERE user_id = :user_id"),
        {"user_id": current.id},
    )
    if float(result.scalar() or 0) < 100:
        raise HTTPException(status_code=403, detail="请先完善资料后再进入首页")
    return current


async def get_current_admin(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """校验当前用户拥有有效管理员角色。"""
    result = await db.execute(
        text("""SELECT 1 FROM user_role
                WHERE user_id = :user_id AND role_code = 'admin' AND status = 1
                LIMIT 1"""),
        {"user_id": current.id},
    )
    if not result.scalar():
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current


@dataclass(frozen=True)
class CurrentMatchmakerAdmin:
    account: MatchmakerAdminAccount
    session_id: int
    permissions: frozenset[str] = field(default_factory=frozenset)

    def require(self, permission: str) -> None:
        aliases = {
            "finance.read": {"finance.read", "finance.write"},
            "meeting.read": {"meeting.read", "meeting.write"},
            "community.read": {"community.read", "community.moderate"},
            "matchmaker.read": {"matchmaker.read", "matchmaker.manage"},
            "matchmaker.product.read": {"matchmaker.product.read", "matchmaker.product.manage"},
            "matchmaker.service.read": {"matchmaker.service.read", "matchmaker.service.manage"},
            "matchmaker.organization.read": {"matchmaker.organization.read", "matchmaker.organization.manage"},
            "matchmaker.member.read": {"matchmaker.member.read", "matchmaker.member.manage"},
            "community.activity.read": {"community.activity.read", "community.activity.manage"},
            "community.moderate": {"community.moderate", "admin.moderate"},
            "reward.read": {"reward.read", "reward.write", "matchmaker.reward.read", "matchmaker.reward.manage"},
            "matchmaker.apportion.read": {"matchmaker.apportion.read", "matchmaker.apportion.write"},
            "commission.read": {"commission.read", "commission.write"},
            "message.read": {"message.read", "message.manage", "message.moderate"},
            "merchant.read": {"merchant.read", "merchant.manage"},
            "video.read": {"video.read", "video.manage"},
            "matchmaker.system.read": {"matchmaker.system.read", "matchmaker.system.manage"},
        }
        allowed = aliases.get(permission, {permission})
        if "*" not in self.permissions and not (allowed & self.permissions):
            raise HTTPException(status_code=403, detail=f"缺少权限：{permission}")


    def require_any(self, *permissions: str) -> None:
        """任一权限命中即通过；全部未命中时抛出 403。"""
        if "*" in self.permissions:
            return
        for permission in permissions:
            try:
                self.require(permission)
            except HTTPException:
                continue
            return
        raise HTTPException(status_code=403, detail=f"缺少权限：{' 或 '.join(permissions)}")

    def scope_clause(
        self,
        params: dict[str, object],
        *,
        user_column: str | None = None,
        organization_column: str = "organization_id",
        store_organization_column: str | None = None,
        user_param: str = "scope_matchmaker_user_id",
    ) -> str:
        """Return a SQL predicate for the account's declared data scope.

        四档语义（唯一实现，供各后台服务共用，禁止各自另写一份）：

        - ``ALL`` / 持 ``*`` ：``1 = 1``
        - ``SELF``           ：``user_column = :<user_param>``；账号未绑定红娘用户时
                               ``1 = 0``（fail-closed，避免退化为按账号 ID 任意匹配）
        - ``STORE``          ：``store_organization_column`` 命中本门店（账号所属组织必须是
                               门店），否则 ``1 = 0``
        - ``ORGANIZATION``   ：``organization_column IN (本组织, 其下属组织)``
        - 未绑定组织         ：``1 = 0``（fail-closed，绝不退化为 ``1 = 1``）

        所有值均通过 ``params`` 绑定，不接受调用方拼接字面量。``user_param`` 仅用于
        兼容既有调用方已固定的绑定变量名。
        """
        account = self.account
        if account.data_scope == "ALL" or "*" in self.permissions:
            return "1 = 1"
        if account.data_scope == "SELF":
            if user_column is None:
                raise HTTPException(status_code=403, detail="当前账号为 SELF 数据范围，该接口缺少用户维度")
            if account.matchmaker_user_id is None:
                return "1 = 0"
            params[user_param] = account.matchmaker_user_id
            return f"{user_column} = :{user_param}"
        if account.data_scope == "STORE":
            column = store_organization_column or organization_column
            if account.organization_id is None:
                return "1 = 0"
            params["scope_store_id"] = account.organization_id
            # 组织必须是门店；平台级账号配置为 STORE 属配置错误，按无数据返回。
            return (
                f"{column} IN (SELECT id FROM organization "
                "WHERE id = :scope_store_id AND org_type = 'store')"
            )
        if account.organization_id is None:
            return "1 = 0"
        params["scope_organization_id"] = account.organization_id
        return (
            f"{organization_column} IN (SELECT id FROM organization "
            "WHERE id = :scope_organization_id OR parent_id = :scope_organization_id)"
        )

    def scope_condition(
        self,
        *,
        organization_column: str,
        params: dict[str, object],
        user_column: str | None = None,
    ) -> str:
        """Return a SQL predicate for the account's declared data scope.

        保留原有签名（既有调用方众多），实现统一委托给 :meth:`scope_clause`。
        """
        return self.scope_clause(
            params,
            user_column=user_column,
            organization_column=organization_column,
            store_organization_column=organization_column,
        )

    def scope_organization_clause(self, params: dict[str, object], *, column: str) -> str:
        """只按「组织维度」给出作用域谓词（资源归组织所有，不存在个人归属）。

        适用于门店、组织、门店提现账户、门店分成行等「没有个人所有者」的资源：

        - ``ALL`` / ``*``          → ``1 = 1``
        - ``STORE``                → ``column IN (本门店)``（组织必须是门店）
        - ``ORGANIZATION``         → ``column IN (本组织及其下属门店)``
        - ``SELF``                 → ``1 = 0``（该资源不存在个人归属，fail-closed，
                                      绝不退化为「按账号个人 ID 匹配组织 ID」）
        - 未绑定组织                → ``1 = 0``

        四档判定仍与 :meth:`scope_clause` 同源，本方法只是裁剪掉用户维度。
        """
        account = self.account
        if account.data_scope == "ALL" or "*" in self.permissions:
            return "1 = 1"
        if account.organization_id is None:
            return "1 = 0"
        if account.data_scope == "STORE":
            params["scope_store_id"] = account.organization_id
            return (
                f"{column} IN (SELECT id FROM organization "
                "WHERE id = :scope_store_id AND org_type = 'store')"
            )
        if account.data_scope == "SELF":
            return "1 = 0"
        params["scope_organization_id"] = account.organization_id
        return (
            f"{column} IN (SELECT id FROM organization "
            "WHERE id = :scope_organization_id OR parent_id = :scope_organization_id)"
        )

    def scope_exists_clause(
        self,
        params: dict[str, object],
        *,
        correlation: str,
        assignment_alias: str = "scope_assignment",
        user_column: str | None = "matchmaker_id",
        organization_column: str = "organization_id",
        user_param: str = "scope_matchmaker_user_id",
    ) -> str:
        """把四档作用域包成基于 ``resource_assignment`` 的 EXISTS 谓词。

        适用于「被保护对象本身不带归属列，归属信息只存在于 ``resource_assignment``」
        的场景（消息、会员写操作等）。四档判定仍由 :meth:`scope_clause` 唯一实现，
        本方法只负责改写 SQL 形态，避免每个服务各写一份 EXISTS 包装。

        :param correlation: assignment 行与被保护对象的相关条件，例如
            ``"scope_assignment.user_id = users.id"`` 或
            ``"scope_assignment.user_id IN (chat_message.from_user_id, chat_message.to_user_id)"``。
        :param user_param: 透传给 :meth:`scope_clause`，仅用于兼容既有绑定变量名。
        :returns: ``1 = 1``（ALL / ``*``，不做归属约束）或 EXISTS 谓词。
        """
        scope = self.scope_clause(
            params,
            user_column=(
                f"{assignment_alias}.{user_column}" if user_column else None
            ),
            organization_column=f"{assignment_alias}.{organization_column}",
            user_param=user_param,
        )
        if scope == "1 = 1":
            return "1 = 1"
        return (
            f"EXISTS (SELECT 1 FROM resource_assignment {assignment_alias} "
            f"WHERE {assignment_alias}.status = 1 AND ({scope}) AND {correlation})"
        )


PERMISSION_ATTR = "__matchmaker_permission__"

# 显式豁免白名单：仅认证类端点可跳过权限校验。
# 这些端点本身就是要「用当前登录态回答我是谁」，因此不能要求业务权限。
# 采用「精确后缀」而非子串匹配：早期实现使用 `"/auth/" in path`，
# 会把 `/admin/members/auth/realname-reviews` 等实名审核业务路由一并误判为豁免。
PERMISSION_EXEMPT_SUFFIXES: tuple[str, ...] = (
    "/admin/matchmaker/auth/me",
    "/admin/matchmaker/auth/logout",
)


def _is_permission_exempt(path: str) -> bool:
    return any(path.endswith(suffix) for suffix in PERMISSION_EXEMPT_SUFFIXES)


def declare_permission(*permissions: str) -> Callable[[Any], Any]:
    """Declare the permission(s) required by a back-office endpoint.

    用法::

        @router.patch("/configs/{namespace}")
        @declare_permission("platform.config.write")
        async def write_config(...): ...

    传入多个权限码时表示「任一命中即可」，例如同时服务首页大盘与会员报表的接口::

        @declare_permission("dashboard.read", "matchmaker.member.read")

    该标记由 :func:`get_current_matchmaker_admin` 统一读取并执行，用于覆盖
    ``_matchmaker_admin_permission`` 的 URL 关键词映射未命中的路径。未命中映射
    且未声明权限的后台路由会被直接拒绝（fail-closed），而不是静默放行。
    """
    if not permissions:
        raise ValueError("declare_permission 至少需要一个权限码")
    declared: str | tuple[str, ...] = permissions[0] if len(permissions) == 1 else tuple(permissions)

    def decorator(func: Any) -> Any:
        setattr(func, PERMISSION_ATTR, declared)
        return func

    return decorator


def _declared_permission(request: Request) -> str | tuple[str, ...] | None:
    """Read the permission(s) declared on the matched endpoint, if any."""
    route = request.scope.get("route")
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return None
    declared = getattr(endpoint, PERMISSION_ATTR, None)
    if not declared:
        return None
    if isinstance(declared, str):
        return declared
    return tuple(str(item) for item in declared)


def _matchmaker_admin_permission(request: Request) -> str | None:
    path = request.url.path
    method = request.method.upper()
    if _is_permission_exempt(path):
        return None
    if path == "/api/v1/admin/dashboard/stats":
        return "matchmaker.read"
    if path == "/api/v1/admin/matchmaker/statistics":
        return "matchmaker.read"
    if "/accounts" in path:
        return "matchmaker.account.manage"
    if "/members" in path:
        return "matchmaker.member.manage" if method != "GET" else "matchmaker.member.read"
    if "/match-records" in path:
        return "matchmaker.service.manage" if method != "GET" else "matchmaker.service.read"
    if "/matchmakers" in path:
        return "matchmaker.manage" if method != "GET" else "matchmaker.read"
    if "/promoters" in path:
        return "matchmaker.manage" if method != "GET" else "matchmaker.read"
    if "/promoter-levels" in path:
        return "matchmaker.manage" if method != "GET" else "matchmaker.read"
    # 合伙红娘：/partner-levels 与 /partner-relations 均不含 "/partners" 子串，顺序无冲突
    if "/partner-levels" in path:
        return "matchmaker.manage" if method != "GET" else "matchmaker.read"
    if "/partner-relations" in path:
        return "matchmaker.manage" if method != "GET" else "matchmaker.read"
    if "/partners" in path:
        return "matchmaker.manage" if method != "GET" else "matchmaker.read"
    if "/offline-vips" in path:
        return "matchmaker.member.manage" if method != "GET" else "matchmaker.member.read"
    if "/service-products" in path:
        return "matchmaker.product.manage" if method != "GET" else "matchmaker.product.read"
    if "/service-requests" in path:
        return "matchmaker.service.manage" if method != "GET" else "matchmaker.service.read"
    if "/branches" in path or "/stores" in path or "/assignments" in path:
        return "matchmaker.organization.manage" if method != "GET" else "matchmaker.organization.read"
    if "/meetings" in path:
        return "meeting.write" if method != "GET" else "meeting.read"
    if "/promotion-orders" in path:
        return "matchmaker.service.manage" if method != "GET" else "matchmaker.service.read"
    if "/finance" in path:
        return "finance.write" if method != "GET" else "finance.read"
    if "/activities" in path:
        return "community.activity.manage" if method != "GET" else "community.activity.read"
    # M7：活动报名（互选活动 / 活动报名）
    if "/mutual-" in path or "/activity-signups" in path:
        return "community.activity.manage" if method != "GET" else "community.activity.read"
    # M7：商家联盟（商家/商品/订单/商家分类）
    if "/merchant" in path:
        return "merchant.manage" if method != "GET" else "merchant.read"
    # M7：短视频（视频/评论/打赏/红包/会员主页）
    if "/short-video" in path or "/video-red-packets" in path:
        return "video.manage" if method != "GET" else "video.read"
    if "/messages" in path or "/announcements" in path:
        return "message.manage" if method != "GET" else "message.read"
    if "/community" in path or "/reports" in path or "/media/" in path:
        return "community.moderate" if method != "GET" else "community.read"
    if "/reward-rules" in path:
        return "reward.write" if method != "GET" else "reward.read"
    if "/apportion-config" in path:
        return "matchmaker.apportion.write" if method != "GET" else "matchmaker.apportion.read"
    if "/commission-levels" in path:
        return "commission.write" if method != "GET" else "commission.read"
    if "/user-cancellations" in path:
        return "matchmaker.member.manage" if method != "GET" else "matchmaker.member.read"
    if "/tickets" in path:
        return "matchmaker.system.manage" if method != "GET" else "matchmaker.system.read"
    return None


async def get_current_matchmaker_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentMatchmakerAdmin:
    """Require the independent red-matchmaker back-office session."""
    if not credentials:
        raise HTTPException(status_code=401, detail="请先登录红娘后台")
    try:
        payload = decode_matchmaker_admin_token(credentials.credentials)
        account_id = int(payload["sub"])
        session_id = int(payload["sid"])
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="无效或已过期的红娘后台访问令牌") from exc
    account = await get_session_account(db, account_id, session_id)
    result = await db.execute(
        text("SELECT permission FROM matchmaker_admin_permission WHERE account_id = :id"),
        {"id": account_id},
    )
    permissions = frozenset(str(row[0]) for row in result.all())
    current = CurrentMatchmakerAdmin(
        account=account,
        session_id=session_id,
        permissions=permissions,
    )
    permission = _matchmaker_admin_permission(request)
    if permission is None:
        permission = _declared_permission(request)
    if isinstance(permission, tuple):
        current.require_any(*permission)
    elif permission is not None:
        current.require(permission)
    elif not _is_permission_exempt(request.url.path):
        # fail-closed：未命中 URL 权限映射、端点也未声明权限时直接拒绝。
        # 早期实现在此处静默放行（fail-open），导致大量后台路由实际无权限校验。
        raise HTTPException(
            status_code=403,
            detail=f"后台接口缺少权限声明：{request.method.upper()} {request.url.path}",
        )
    return current

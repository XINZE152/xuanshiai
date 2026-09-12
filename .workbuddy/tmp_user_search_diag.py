"""临时验证：统一用户搜索 + 手输账号解析。"""
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.services import user_candidates
from app.services.promoter_staff_admin import search_user_candidates as promoter_search

URL = "mysql+aiomysql://root:051019@127.0.0.1:3306/xuanshiai"


async def main() -> None:
    engine = create_async_engine(URL)
    async with AsyncSession(engine) as db:
        for kw in ("测试", "用户", "1", "2", "1973", "不存在的名字"):
            for scope in ("service_matchmaker", "promoter", "partner"):
                rows = await user_candidates.search_user_candidates(db, kw, scope)  # type: ignore[arg-type]
                print(f"kw={kw!r} scope={scope} -> {len(rows)}", [(r['id'], r['nickname'], r['unavailable'], r['unavailable_reason']) for r in rows])

        print("--- promoter service 封装 ---")
        items = await promoter_search(db, "测试")
        for it in items:
            print(it.model_dump())

        print("--- resolve_user_id 手输解析 ---")
        for kw, by in (("测试用户", "nickname"), ("测试用", "nickname"), ("19730552884", "phone"), ("1973", "phone"), ("2", "nickname"), ("xxxyyy", "nickname")):
            try:
                uid = await user_candidates.resolve_user_id(db, kw, by)
                print(f"  输入={kw!r} by={by} -> user_id={uid}")
            except Exception as e:  # noqa: BLE001
                print(f"  输入={kw!r} by={by} -> {type(e).__name__} {getattr(e, 'detail', e)}")
    await engine.dispose()


asyncio.run(main())

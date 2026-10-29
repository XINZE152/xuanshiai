"""支付域单元测试的轻量 FakeSession。

项目没有可直接复用的 ``db_session`` fixture（单元测试默认不连 MySQL），因此
这里提供一个只记录 SQL/参数、按测试回调返回结果的异步 Session 替身：

- 不连接数据库、不执行真实 SQL；
- 支持 ``execute/scalar/commit/rollback/begin/begin_nested``；
- 测试通过 ``responder`` / ``scalar_responder`` 返回预置结果；
- 通过 ``statements`` 断言实际执行过的 SQL 与参数。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

Responder = Callable[[str, dict[str, Any], "FakeSession"], Awaitable[Any]]
ScalarResponder = Callable[[str, dict[str, Any], "FakeSession"], Awaitable[Any]]


class FakeResult:
    """模拟 SQLAlchemy CursorResult 的最小子集。"""

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        first: dict[str, Any] | None = None,
        rowcount: int = 1,
        lastrowid: int = 1,
    ) -> None:
        self._rows = list(rows or [])
        self._first = first if first is not None else (self._rows[0] if self._rows else None)
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._first

    def one(self) -> dict[str, Any]:
        if self._first is None:
            raise AssertionError("FakeResult.one() 没有结果")
        return self._first


class FakeTransaction:
    """``async with session.begin()`` 替身。"""

    def __init__(self, session: "FakeSession") -> None:
        self.session = session

    async def __aenter__(self) -> None:
        self.session.begins += 1

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.session.rollbacks += 1
        else:
            self.session.commits += 1
        return False


class FakeSession:
    """可配置的异步 Session 替身。"""

    def __init__(
        self,
        responder: Responder | None = None,
        scalar_responder: ScalarResponder | None = None,
    ) -> None:
        self.responder = responder
        self.scalar_responder = scalar_responder
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.begins = 0

    def begin(self) -> FakeTransaction:
        return FakeTransaction(self)

    def begin_nested(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        sql = " ".join(str(statement).split())
        values = dict(params or {})
        self.statements.append((sql, values))
        if self.responder is not None:
            return await self.responder(sql, values, self)
        return FakeResult()

    async def scalar(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        values = dict(params or {})
        self.statements.append((sql, values))
        if self.scalar_responder is not None:
            return await self.scalar_responder(sql, values, self)
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    # ---- 断言辅助 ----
    def sql_text(self) -> str:
        return "\n".join(sql for sql, _ in self.statements)

    def params_for(self, fragment: str) -> list[dict[str, Any]]:
        return [params for sql, params in self.statements if fragment in sql]

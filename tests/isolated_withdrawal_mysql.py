"""Run only against a disposable MySQL instance using the dedicated /tmp socket."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import text

url = os.environ.get('DATABASE_URL', '')
if os.environ.get('TEST_WITHDRAWAL_ISOLATED') != '1' or 'unix_socket=/tmp/xsa-withdraw-test.' not in url:
    raise RuntimeError('Refusing to run outside the dedicated isolated MySQL socket')

from app.db.business_schema import BUSINESS_TABLES  # noqa: E402
from app.db.session import engine, session_factory  # noqa: E402
from app.schemas.finance import WithdrawalCreate  # noqa: E402
from app.services.finance import get_balance, request_withdrawal  # noqa: E402

MIGRATIONS = Path('migrations/business')


async def main():
    async with engine.begin() as conn:
        await conn.execute(text('CREATE TABLE users (id bigint unsigned PRIMARY KEY) ENGINE=InnoDB'))
        await conn.execute(text('CREATE TABLE admin_config_snapshot (namespace varchar(128) PRIMARY KEY, config_json JSON) ENGINE=InnoDB'))
        for table in ('account_ledger', 'withdrawal_request', 'withdrawal_event'):
            await conn.execute(text(BUSINESS_TABLES[table]))
        previous = (MIGRATIONS / '20261116_01_withdrawal_payee_verify_previous.sql').read_text()
        assert (await conn.execute(text(previous))).first() is None
        up = (MIGRATIONS / '20261116_01_withdrawal_payee_up.sql').read_text()
        verify = (MIGRATIONS / '20261116_01_withdrawal_payee_verify.sql').read_text()
        down = (MIGRATIONS / '20261116_01_withdrawal_payee_down.sql').read_text()
        await conn.execute(text(up))
        assert (await conn.execute(text(verify))).first() is None
        await conn.execute(text(down))
        assert (await conn.execute(text(previous))).first() is None
        await conn.execute(text(up))
        await conn.execute(text("INSERT INTO users (id) VALUES (1)"))
        config = {'withdrawal': {'enabled': True, 'min_amount': '0', 'fee_mode': 'none'},
                  'withdraw_methods': {'manual_wechat': {'enabled': True, 'min_amount': '1', 'max_amount': '1000'}}}
        await conn.execute(text("INSERT INTO admin_config_snapshot (namespace,config_json) VALUES ('finance', :config)"),
                           {'config': json.dumps(config)})
        await conn.execute(text("""INSERT INTO account_ledger
            (account_type,account_id,direction,amount,state,source_type,source_id,idempotency_key)
            VALUES ('user',1,'CREDIT',100,'AVAILABLE','test',1,'test-credit')"""))

    actor = SimpleNamespace(id=1)
    request = WithdrawalCreate(amount='20.00', method='wechat', account='example', payee_name='测试')

    async def submit(key, body):
        async with session_factory() as db:
            try:
                return await request_withdrawal(db, actor, body, key)
            except HTTPException as exc:
                await db.rollback()
                return exc

    first, second = await asyncio.gather(submit('same-key-123', request), submit('same-key-123', request))
    assert not isinstance(first, HTTPException) and not isinstance(second, HTTPException)
    assert first.id == second.id
    invalid = await submit('same-key-123', WithdrawalCreate(amount='21', method='wechat', account='example', payee_name='测试'))
    assert isinstance(invalid, HTTPException) and invalid.status_code == 409
    sixty = WithdrawalCreate(amount='60', method='wechat', account='example', payee_name='测试')
    a, b = await asyncio.gather(submit('different-key-a', sixty), submit('different-key-b', sixty))
    assert sum(isinstance(item, HTTPException) and item.status_code == 409 for item in (a, b)) == 1
    async with session_factory() as db:
        balance = await get_balance(db, 'user', 1)
        assert str(balance.available_amount) == '20.00'
        assert await db.scalar(text('SELECT COUNT(*) FROM withdrawal_request')) == 2
        assert await db.scalar(text('SELECT COUNT(*) FROM withdrawal_payee_secret')) == 2
        assert await db.scalar(text('SELECT COUNT(*) FROM withdrawal_event')) == 2
    await engine.dispose()
    print('isolated migration up/down and concurrent withdrawal checks passed')


if __name__ == '__main__':
    asyncio.run(main())

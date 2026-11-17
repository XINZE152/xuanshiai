"""Exercise personal history SQL on a disposable, socket-only MySQL instance."""

import asyncio
import os

from sqlalchemy import text

if os.environ.get('TEST_WITHDRAWAL_ISOLATED') != '1' or 'unix_socket=/tmp/xsa-withdraw-test.' not in os.environ.get('DATABASE_URL', ''):
    raise RuntimeError('Refusing to run outside the dedicated isolated MySQL socket')

from app.db.business_schema import BUSINESS_TABLES  # noqa: E402
from app.db.session import engine, session_factory  # noqa: E402
from app.services.finance import list_user_finance_records  # noqa: E402


async def main():
    async with engine.begin() as conn:
        for table in ('account_ledger', 'withdrawal_request'):
            await conn.execute(text(BUSINESS_TABLES[table]))
        await conn.execute(text("""CREATE TABLE payment_order (id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL,
            amount DECIMAL(10,2), paid_amount DECIMAL(12,2), status INT, pay_time DATETIME,
            pay_type INT, channel VARCHAR(16), product_name VARCHAR(128), refund_status VARCHAR(16)) ENGINE=InnoDB"""))
        await conn.execute(text("""INSERT INTO withdrawal_request (id,account_type,account_id,amount,status)
            VALUES (1,'user',1,20,'REJECTED')"""))
        await conn.execute(text("""INSERT INTO account_ledger (account_type,account_id,direction,amount,state,source_type,source_id,idempotency_key)
            VALUES ('user',1,'DEBIT',20,'AVAILABLE','withdrawal',1,'test-wd'),
            ('user',1,'CREDIT',20,'AVAILABLE','withdrawal_reversal',1,'test-reverse'),
            ('user',2,'CREDIT',500,'AVAILABLE','commission',2,'test-other')"""))
        await conn.execute(text("""INSERT INTO payment_order (id,user_id,amount,paid_amount,status,pay_time,pay_type,channel,product_name)
            VALUES (1,1,10,10,1,UTC_TIMESTAMP(),1,'wechat','服务'),
            (2,1,20,NULL,3,UTC_TIMESTAMP(),2,'balance','会员'),
            (3,1,30,30,1,UTC_TIMESTAMP(),4,'mock','模拟'),
            (4,2,50,50,1,UTC_TIMESTAMP(),1,'wechat','其他人')"""))
        await conn.execute(text("UPDATE payment_order SET refund_status='PARTIAL' WHERE id=1"))
    async with session_factory() as db:
        ledger = await list_user_finance_records(db, 1, 'balance', 1, 20)
        assert ledger.total == 2 and {r.title for r in ledger.items} == {'提现申请', '提现退回'}
        assert all(r.status == 'REJECTED' for r in ledger.items)
        expenses = await list_user_finance_records(db, 1, 'expense', 1, 20)
        assert expenses.total == 2 and {r.title for r in expenses.items} == {'服务', '会员'}
        assert {r.status for r in expenses.items} == {'PARTIAL_REFUND', 'REFUNDED'}
        next_page = await list_user_finance_records(db, 1, 'balance', 3, 1)
        assert not next_page.items and not next_page.has_more
    await engine.dispose()
    print('isolated finance history SQL checks passed')


if __name__ == '__main__':
    asyncio.run(main())

"""Withdrawal retries cannot duplicate deductions or change a request payload."""

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from app.schemas.finance import WithdrawalCreate
from app.services import finance


class Result:
    def __init__(self, row=None, lastrowid=123):
        self.row = row
        self.lastrowid = lastrowid

    def mappings(self):
        return self

    def first(self):
        return self.row

    def one(self):
        return self.row


class DB:
    def __init__(self, replay=None):
        self.replay = replay
        self.calls = []
        self.committed = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        params = dict(params or {})
        self.calls.append((sql, params))
        if 'FROM withdrawal_event we JOIN' in sql:
            return Result(self.replay)
        if 'SELECT id, account_type, account_id, amount, status,' in sql:
            return Result({'id': 123})
        return Result()

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_submit_saves_payee_ledger_and_idempotency_in_one_transaction(monkeypatch):
    db = DB()
    monkeypatch.setattr(finance.settings, 'withdrawal_payee_encryption_key', SecretStr(Fernet.generate_key().decode()))
    async def policy(_db):
        return {'enabled': True, 'min_amount': '0'}
    async def balance(_db, _kind, _id):
        return SimpleNamespace(available_amount='100')
    monkeypatch.setattr(finance, 'withdrawal_policy', policy)
    monkeypatch.setattr(finance, 'get_balance', balance)
    monkeypatch.setattr(finance, 'encrypt_sensitive', lambda _: 'encrypted')
    monkeypatch.setattr(finance, '_withdrawal', lambda row: row)
    request = WithdrawalCreate(amount='10.00', method='wechat', account='wx-user', payee_name='张三')
    await finance.request_withdrawal(db, SimpleNamespace(id=1), request, 'request-key-123')
    assert 'FOR UPDATE' in db.calls[0][0]
    assert db.committed
    sql = '\n'.join(item[0] for item in db.calls)
    assert 'INSERT INTO withdrawal_payee_secret' in sql
    assert 'INSERT INTO account_ledger' in sql
    assert 'INSERT INTO withdrawal_event' in sql
    assert any(params.get('ciphertext') == 'encrypted' for _, params in db.calls)


@pytest.mark.asyncio
async def test_reused_key_with_changed_payload_cannot_debit(monkeypatch):
    db = DB(replay={'payload_hash': 'not-the-request-hash'})
    monkeypatch.setattr(finance.settings, 'withdrawal_payee_encryption_key', SecretStr(Fernet.generate_key().decode()))
    request = WithdrawalCreate(amount='10.00', method='wechat', account='wx-user', payee_name='张三')
    with pytest.raises(HTTPException) as exc:
        await finance.request_withdrawal(db, SimpleNamespace(id=1), request, 'request-key-123')
    assert exc.value.status_code == 409
    assert not db.committed
    assert not any('INSERT INTO' in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_retry_with_same_key_returns_existing_request(monkeypatch):
    key = SecretStr(Fernet.generate_key().decode())
    monkeypatch.setattr(finance.settings, 'withdrawal_payee_encryption_key', key)
    request = WithdrawalCreate(amount='10.00', method='wechat', account='wx-user', payee_name='张三')
    canonical = request.model_dump(mode='json')
    canonical['amount'] = '10.00'
    digest = hmac.new(key.get_secret_value().encode(), json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
    db = DB(replay={'payload_hash': digest, 'id': 123})
    monkeypatch.setattr(finance, '_withdrawal', lambda row: row)
    result = await finance.request_withdrawal(db, SimpleNamespace(id=1), request, 'request-key-123')
    assert result['id'] == 123
    assert not any('INSERT INTO' in sql for sql, _ in db.calls)
    assert not db.committed


def test_payee_encryption_requires_key_and_round_trips(monkeypatch):
    monkeypatch.setattr(finance.settings, 'withdrawal_payee_encryption_key', None)
    with pytest.raises(HTTPException) as exc:
        finance.encrypt_sensitive('secret')
    assert exc.value.status_code == 503
    monkeypatch.setattr(finance.settings, 'withdrawal_payee_encryption_key', SecretStr(Fernet.generate_key().decode()))
    encrypted = finance.encrypt_sensitive('secret')
    assert encrypted != 'secret'
    assert finance.decrypt_sensitive(encrypted) == 'secret'


def test_masked_only_request_is_rejected():
    with pytest.raises(ValidationError):
        WithdrawalCreate(amount='10', payee_masked='wechat / ****1234')

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.payment.base import PaymentValidationError
from app.services.payment.wechat_v3 import WechatPayV3Client


@pytest.mark.asyncio
async def test_transfer_gateway_uses_stable_bill_and_does_not_retry_post():
    client = object.__new__(WechatPayV3Client)
    client.config = SimpleNamespace(app_id='test-app')
    client._request = AsyncMock(return_value={'state': 'WAIT_USER_CONFIRM'})
    result = await client.create_merchant_transfer(out_bill_no='WD123', openid='openid',
        amount_cents=100, scene_id='1000', remark='佣金提现', notify_url='https://example.test/callback',
        scene_report_infos=[{'info_type': '说明', 'info_content': '测试'}])
    assert result['state'] == 'WAIT_USER_CONFIRM'
    args = client._request.await_args
    assert args.args[0:2] == ('POST', '/v3/fund-app/mch-transfer/transfer-bills')
    assert args.args[2]['out_bill_no'] == 'WD123'
    assert args.args[2]['transfer_amount'] == 100
    assert not args.kwargs.get('retryable', False)


@pytest.mark.asyncio
async def test_transfer_gateway_validates_and_queries_bill():
    client = object.__new__(WechatPayV3Client)
    client._request = AsyncMock(return_value={'state': 'SUCCESS'})
    with pytest.raises(PaymentValidationError):
        await client.query_merchant_transfer('../bad')
    await client.query_merchant_transfer('WD123')
    assert client._request.await_args.args[1].endswith('/out-bill-no/WD123')
    assert client._request.await_args.kwargs['retryable'] is True

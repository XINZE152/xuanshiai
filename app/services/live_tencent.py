"""腾讯云 TRTC 服务端凭证适配。"""

import base64
import hashlib
import hmac
import json
import time
import zlib

from app.core.config import settings


class LiveProviderUnavailable(Exception):
    pass


def generate_user_sig(user_id: str) -> tuple[int, str, int]:
    app_id = settings.tencent_live_sdk_app_id
    secret = settings.tencent_live_secret_key
    if not settings.live_enabled or not app_id or secret is None:
        raise LiveProviderUnavailable("直播或腾讯云 TRTC 尚未配置")
    issued_at = int(time.time())
    ttl = settings.tencent_live_user_sig_ttl_seconds
    content = f"TLS.identifier:{user_id}\nTLS.sdkappid:{app_id}\nTLS.time:{issued_at}\nTLS.expire:{ttl}\n"
    signature = base64.b64encode(hmac.new(secret.get_secret_value().encode(), content.encode(), hashlib.sha256).digest()).decode()
    payload = {"TLS.ver": "2.0", "TLS.identifier": user_id, "TLS.sdkappid": app_id, "TLS.expire": ttl, "TLS.time": issued_at, "TLS.sig": signature}
    encoded = base64.b64encode(zlib.compress(json.dumps(payload, separators=(",", ":")).encode())).decode()
    return app_id, encoded.replace("+", "*").replace("/", "-").replace("=", "_"), ttl

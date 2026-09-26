"""手动风险复现：验证 /storage/uploads 私有类媒体止损与公开类边界。

运行：python -m scripts.reproduce_upload_exposure
只写入 tempfile，不启动服务、不连接数据库、不读取本机上传目录。

止损已实施（MEDIA_STORAGE_ACCESS_REVIEW.md §5 第 1 步）：跟进附件类
（{member_id}/follow-up-img-*、follow-up-voice-*）匿名直连返回 403；
公开资料媒体保持匿名 200。
"""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from fastapi.testclient import TestClient


def main() -> None:
    with TemporaryDirectory() as directory:
        # 必须在导入 app.main 前设置，避免模块级 app 使用本机 UPLOAD_DIR。
        os.environ["ENVIRONMENT"] = "testing"
        os.environ["AUTO_INIT_DB"] = "false"
        os.environ["UPLOAD_DIR"] = directory
        from app.main import create_app

        fixtures = {
            "42/follow-up-img-example.webp": b"private-follow-up-image-sentinel",
            "42/follow-up-voice-example.mp3": b"private-follow-up-voice-sentinel",
            "42/photo-example.webp": b"ordinary-public-image-sentinel",
        }
        # 私有类（跟进附件）匿名直连必须 403；公开类保持 200 且字节一致。
        expected_status = {
            "42/follow-up-img-example.webp": 403,
            "42/follow-up-voice-example.mp3": 403,
            "42/photo-example.webp": 200,
        }
        for relative_path, payload in fixtures.items():
            target = Path(directory) / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

        # 不进入 lifespan；AUTO_INIT_DB=false 双保险，禁止初始化数据库。
        client = TestClient(create_app())
        for relative_path, payload in fixtures.items():
            url = f"/storage/uploads/{relative_path}"
            response = client.get(url)  # 无 Cookie、Authorization 或其他身份信息
            expected = expected_status[relative_path]
            bytes_match = response.content == payload
            print(f"anonymous GET {url}: {response.status_code}, bytes_match={bytes_match}")
            assert response.status_code == expected, (
                f"预期 {url} 匿名访问返回 {expected}；实际 {response.status_code}"
            )
            if expected == 200:
                assert bytes_match, f"公开类 {url} 字节不一致"
            else:
                assert response.content != payload, f"私有类 {url} 403 响应体不应是原文件"
        traversal = client.get('/storage/uploads/../outside.txt')
        missing = client.get('/storage/uploads/42/does-not-exist.webp')
        print(f'anonymous traversal GET: {traversal.status_code}')
        print(f'anonymous missing GET: {missing.status_code}')
        assert traversal.status_code in {400, 404}
        assert missing.status_code == 404


if __name__ == "__main__":
    main()

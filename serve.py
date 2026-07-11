import os
import tempfile
from pathlib import Path

from waitress import serve

from app import create_app
from app.config import Config


app = create_app()


def acquire_instance_lock(port):
    lock_path = Path(tempfile.gettempdir()) / f"consumer-analysis-web-{port}.lock"
    lock_file = lock_path.open("a+b")
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_file.close()
        raise RuntimeError(f"端口 {port} 已有本系统服务实例运行") from exc
    return lock_file

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    instance_lock = acquire_instance_lock(port)
    proxy_options = {}
    if Config.TRUST_PROXY:
        proxy_options = {
            "trusted_proxy": "*",
            "trusted_proxy_count": 1,
            "trusted_proxy_headers": "x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port",
        }
    serve(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=port,
        threads=int(os.environ.get("WEB_THREADS", "8")),
        channel_timeout=60,
        clear_untrusted_proxy_headers=True,
        **proxy_options,
    )

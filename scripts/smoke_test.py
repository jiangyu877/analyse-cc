import re
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app


def main():
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if not admin_password:
        raise RuntimeError("请先设置 ADMIN_PASSWORD 再运行登录烟测")
    app = create_app()
    app.config["PROPAGATE_EXCEPTIONS"] = True
    client = app.test_client()

    login_page = client.get("/login")
    match = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data)
    if not match:
        raise RuntimeError("CSRF token was not rendered")

    response = client.post(
        "/login",
        data={
            "csrf_token": match.group(1).decode("utf-8"),
            "username": "admin",
            "password": admin_password,
        },
        follow_redirects=False,
    )
    print(f"login={response.status_code} location={response.headers.get('Location')}")
    dashboard = client.get("/")
    print(f"dashboard={dashboard.status_code} bytes={len(dashboard.data)}")


if __name__ == "__main__":
    main()

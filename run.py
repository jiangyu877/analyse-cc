import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", app.config["HOST"]),
        port=int(os.environ.get("PORT", app.config["PORT"])),
        debug=app.config["DEBUG"],
    )


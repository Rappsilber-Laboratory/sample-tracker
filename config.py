import os

basedir = os.path.abspath(os.path.dirname(__file__))

# Tolerate a known placeholder key only in debug, so the app runs out of the box
# for local development. Anywhere else a real SECRET_KEY must be supplied or we
# refuse to start, rather than silently signing sessions/CSRF tokens with a
# value that is public in the source tree.
_DEV_SECRET = "dev-secret-change-in-production"
_DEBUG = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or (_DEV_SECRET if _DEBUG else None)
    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY is not set. Export a real secret, e.g.\n"
            "    export SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')\n"
            "or run in debug mode (FLASK_DEBUG=1) to use the insecure dev default."
        )
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(basedir, "samples.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

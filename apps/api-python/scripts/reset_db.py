from pathlib import Path
import os
import sys
from sqlalchemy.engine import make_url
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api-python"))
from app.db import DATABASE_URL  # noqa: E402

url = make_url(DATABASE_URL.replace("postgres://", "postgresql://", 1))
migration_url = make_url(
    os.getenv("DIRECT_URL", DATABASE_URL).replace("postgres://", "postgresql://", 1)
)
local_hosts = {"localhost", "127.0.0.1", "postgres"}
if (url.host not in local_hosts or migration_url.host not in local_hosts) and os.getenv(
    "ALLOW_REMOTE_DATABASE_RESET"
) != "true":
    raise SystemExit(
        "refusing to reset a remote database; use a disposable test database instead"
    )
if url.database != migration_url.database:
    raise SystemExit("DATABASE_URL and DIRECT_URL must target the same database")

config = Config(str(ROOT / "apps/api-python/alembic.ini"))
command.downgrade(config, "base")
command.upgrade(config, "head")
print(
    f"Database reset at {migration_url.host}:{migration_url.port or 5432}/"
    f"{migration_url.database}"
)

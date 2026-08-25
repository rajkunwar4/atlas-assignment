"""Apply the canonical Alembic schema to the disposable test database."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
test_url = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://atlas:atlas@localhost:55432/atlas_test?sslmode=disable",
)
os.environ["DATABASE_URL"] = test_url
os.environ["DIRECT_URL"] = test_url
command.upgrade(Config(str(ROOT / "apps/api-python/alembic.ini")), "head")

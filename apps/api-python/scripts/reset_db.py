from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api-python"))
from app.db import Base, engine  # noqa: E402
from app import models  # noqa: E402, F401

Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
print("Python database reset")

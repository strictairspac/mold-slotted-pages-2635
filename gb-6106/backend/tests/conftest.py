import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db}")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DEMO_PASSWORD", "Demo-DbWorkbench-2026")
os.environ.setdefault("SEED_DEMO", "false")


@pytest.fixture()
def db():
    from src.config.resources import engine
    from src.models.base import metadata
    from src.models import audit, cases, reviews, runs, users  # noqa: F401  (register tables)
    from src.domain.fixtures import FIXTURES
    from src.models.cases import cases as cases_table

    metadata.drop_all(engine)
    metadata.create_all(engine)
    with engine.begin() as connection:
        for fixture in FIXTURES:
            connection.execute(cases_table.insert().values(**fixture, created_by="seed"))
    yield engine
    metadata.drop_all(engine)

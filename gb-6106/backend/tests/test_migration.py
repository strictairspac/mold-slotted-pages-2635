import sqlalchemy as sa
from sqlalchemy import inspect, text

from src.models.runs import runs as runs_table
from migrations.v001_workbench import upgrade
from migrations.v002_replay_checkpoints import NEW_COLUMNS


def test_v002_upgrades_an_existing_runs_table(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path/'legacy.db'}")
    legacy_columns = [column for column in runs_table.columns if column.name not in NEW_COLUMNS]
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL,"
            " status VARCHAR(16) NOT NULL, parameter INTEGER NOT NULL, actor VARCHAR(48) NOT NULL,"
            " created_at BIGINT NOT NULL, finished_at BIGINT, result JSON, failure VARCHAR(320), digest VARCHAR(64))"
        ))
        assert legacy_columns  # the legacy table really lacks the new columns
        upgrade(connection)
        names = {column["name"] for column in inspect(connection).get_columns("runs")}
        assert set(NEW_COLUMNS) <= names
        # Idempotent: a restart must not try to re-add columns.
        upgrade(connection)
        versions = connection.execute(text("SELECT version FROM schema_versions")).scalars().all()
        assert versions == ["v002_replay_checkpoints"]

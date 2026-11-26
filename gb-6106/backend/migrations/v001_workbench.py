from time import time

from sqlalchemy import text

from src.models import audit, cases, reviews, runs, users  # Register the tables.
from src.models.base import metadata

VERSIONS = "schema_versions"
MIGRATIONS = ("v002_replay_checkpoints",)


def _applied(connection):
    rows = connection.execute(text(f"SELECT version FROM {VERSIONS}")).scalars()
    return set(rows)


def _mark(connection, version):
    connection.execute(
        text(f"INSERT INTO {VERSIONS} (version, applied_at) VALUES (:version, :applied_at)"),
        {"version": version, "applied_at": int(time())},
    )


def upgrade(connection):
    """Create tables once, then apply each versioned migration exactly once.

    Version 1 is additive and declarative: ``create_all`` creates the four core
    tables and is idempotent.  Later versions change existing tables, so they
    are guarded by the applied versions recorded in ``schema_versions``.
    """
    connection.execute(text(
        f"CREATE TABLE IF NOT EXISTS {VERSIONS} ("
        "version VARCHAR(32) NOT NULL PRIMARY KEY, applied_at BIGINT NOT NULL)"
    ))
    metadata.create_all(connection)
    done = _applied(connection)
    for version in MIGRATIONS:
        if version in done:
            continue
        module = __import__(f"migrations.{version}", fromlist=["upgrade"])
        module.upgrade(connection)
        _mark(connection, version)

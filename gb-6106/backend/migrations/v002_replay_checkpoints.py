"""Add the checkpoint, pause and lease columns to an existing runs table."""
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

from src.models.runs import runs  # Register the table on the metadata.


NEW_COLUMNS = (
    "total_steps", "confirmed_step", "checkpoint", "pause_requested", "halt_reason",
    "epoch", "lease_owner", "lease_until", "started_at", "taken_by", "takeover_count",
)


def upgrade(connection):
    existing = {column["name"] for column in inspect(connection).get_columns(runs.name)}
    preparer = connection.dialect.identifier_preparer
    table = preparer.quote(runs.name)
    for name in NEW_COLUMNS:
        if name in existing:
            continue
        ddl = str(CreateColumn(runs.c[name]).compile(dialect=connection.dialect))
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))

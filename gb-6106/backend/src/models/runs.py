from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, ForeignKey, Integer, String, Table, false, text,
)
from .base import metadata


runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    Column("parameter", Integer, nullable=False),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("finished_at", BigInteger, nullable=True),
    Column("result", JSON(none_as_null=True), nullable=True),
    Column("failure", String(320), nullable=True),
    Column("digest", String(64), nullable=True),
    # Replay progress: the checkpoint is the full page state after the last
    # confirmed step, so a resumed run continues at exactly the same slot.
    Column("total_steps", Integer, nullable=False, server_default=text("0")),
    Column("confirmed_step", Integer, nullable=False, server_default=text("0")),
    Column("checkpoint", JSON(none_as_null=True), nullable=True),
    Column("pause_requested", Boolean, nullable=False, server_default=false()),
    Column("halt_reason", String(24), nullable=True),
    # Lease fencing. Every takeover or claim bumps epoch; writes that carry an
    # older epoch are discarded, so a slow old worker cannot account results.
    Column("epoch", Integer, nullable=False, server_default=text("0")),
    Column("lease_owner", String(64), nullable=True),
    Column("lease_until", BigInteger, nullable=True),
    Column("started_at", BigInteger, nullable=True),
    Column("taken_by", String(48), nullable=True),
    Column("takeover_count", Integer, nullable=False, server_default=text("0")),
)

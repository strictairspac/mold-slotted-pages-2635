from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    # A pause was requested; the live worker stops at its last confirmed step.
    PAUSING = "pausing"
    # Pause confirmed at a step boundary; only an explicit takeover resumes it.
    PAUSED = "paused"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

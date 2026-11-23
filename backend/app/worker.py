"""Standalone job worker: ``python -m app.worker``."""

from __future__ import annotations

import signal
import threading

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services import ingest as _handlers  # noqa: F401  (registers job handlers)
from app.services.jobs import worker_loop


def main() -> None:
    configure_logging(json=get_settings().env == "production")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker_loop(stop)


if __name__ == "__main__":
    main()

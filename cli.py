"""Command line entrypoint (server, jobs, MQTT ingestor): cli."""

from __future__ import annotations

import json
import logging
import sys
import time

from config.database import init_db
from config.settings import get_settings
from integrations.mqtt import MqttIngestor
from modules.jobs import JOBS

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) >= 2 and sys.argv[1] == "job":
        if len(sys.argv) < 3 or sys.argv[2] not in JOBS:
            print(f"Usage: python main.py job <{'|'.join(JOBS)}>")
            sys.exit(1)
        init_db()
        print(json.dumps(JOBS[sys.argv[2]](), default=str, indent=2))
    elif len(sys.argv) >= 2 and sys.argv[1] == "mqtt":
        init_db()
        ingestor = MqttIngestor()
        ingestor.start()
        print("MQTT ingestor listening (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            ingestor.stop()
    else:
        import uvicorn

        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=not get_settings().is_prod)

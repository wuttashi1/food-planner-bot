import os
import sys
import time
from pathlib import Path

heartbeat = Path(os.environ.get("DATA_DIR", "data")) / "heartbeat"
sys.exit(0 if heartbeat.exists() and time.time() - heartbeat.stat().st_mtime < 150 else 1)

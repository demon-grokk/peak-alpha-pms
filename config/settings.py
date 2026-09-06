"""
Configuration settings and environment loader for Peak Alpha PMS.
Re-exports constants from config.constants for seamless system configuration.
"""
import os
from pathlib import Path
from config.constants import *

# Helper to load .env file into os.environ if present
env_file = BASE_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

SCANX_AUTH_TOKEN = os.getenv("SCANX_AUTH_TOKEN", "")

# Ensure data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

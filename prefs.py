"""
User preference storage — persists role and location settings chosen via the bot UI.
Falls back to config.py / .env values if no preferences have been saved yet.
"""

import json
import os
from pathlib import Path

_DATA_DIR   = os.environ.get("DATA_DIR", str(Path(__file__).parent))
_PREFS_FILE = Path(_DATA_DIR) / "user_prefs.json"


def load() -> dict:
    """Return current prefs dict. Falls back to config.py values if not yet set up."""
    try:
        with open(_PREFS_FILE) as f:
            data = json.load(f)
        if data.get("setup_done"):
            return data
    except Exception:
        pass

    # Fall back to .env / config.py
    try:
        from config import JOB_ROLES, LOCATION_TYPE, PREFERRED_LOCATIONS
        return {
            "roles": JOB_ROLES,
            "location_type": LOCATION_TYPE,
            "preferred_locations": PREFERRED_LOCATIONS,
            "setup_done": False,
        }
    except Exception:
        return {
            "roles": ["marketing"],
            "location_type": "remote",
            "preferred_locations": [],
            "setup_done": False,
        }


def save(roles: list, location_type: str, preferred_locations: list) -> None:
    os.makedirs(str(_PREFS_FILE.parent), exist_ok=True)
    with open(_PREFS_FILE, "w") as f:
        json.dump({
            "roles": roles,
            "location_type": location_type,
            "preferred_locations": preferred_locations,
            "setup_done": True,
        }, f)


def is_done() -> bool:
    try:
        with open(_PREFS_FILE) as f:
            return json.load(f).get("setup_done", False)
    except Exception:
        return False

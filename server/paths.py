"""Filesystem layout for JARVIS Android V2.

Everything the user owns (config, secrets, conversations, voices) lives under
JARVIS_HOME (default ~/.jarvis) and is NEVER inside the source tree, so that
re-installing or re-unzipping the project cannot destroy user data.
"""

import os
import stat
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))

CONFIG_FILE = JARVIS_HOME / "config.json"
SECRETS_FILE = JARVIS_HOME / "secrets.json"
CONNECTIONS_FILE = JARVIS_HOME / "connections.json"
CONVERSATIONS_DIR = JARVIS_HOME / "conversations"
VOICES_DIR = Path(os.environ.get("JARVIS_VOICES", JARVIS_HOME / "voices"))
CAPTURES_DIR = JARVIS_HOME / "captures"
LOG_DIR = JARVIS_HOME / "logs"
RUN_DIR = JARVIS_HOME / "run"

_DIRS = (JARVIS_HOME, CONVERSATIONS_DIR, VOICES_DIR, CAPTURES_DIR, LOG_DIR, RUN_DIR)


def ensure_dirs() -> None:
    for d in _DIRS:
        d.mkdir(parents=True, exist_ok=True)
    # The home itself should not be world readable: it holds secrets.json.
    try:
        JARVIS_HOME.chmod(stat.S_IRWXU)
    except OSError:
        pass


def secure_file(path: Path) -> None:
    """Restrict a file to the owner (0600). Best effort: Android/FAT may refuse."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

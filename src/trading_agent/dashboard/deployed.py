"""The deployed `run_while_paused` setting, read from config/schedule.yaml (research D14).

One key is read and nothing else, so the dashboard doesn't depend on the orchestrator's
whole schedule validation (and may not import it). Anything but a real boolean is "unreadable"
(None), which makes resume fail closed.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def run_while_paused(config_dir: Path = DEFAULT_CONFIG_DIR) -> bool | None:
    try:
        data = yaml.safe_load((Path(config_dir) / "schedule.yaml").read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    section = data.get("portfolio_manager")
    if not isinstance(section, dict):
        return None
    value = section.get("run_while_paused")
    return value if isinstance(value, bool) else None

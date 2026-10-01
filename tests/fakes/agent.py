"""A stand-in agent for the real launcher's tests: no model calls, no network.

Run by the launcher as `python -m tests.fakes.agent`. It takes no arguments, only
the environment the test builds (with obviously fake values), and reads its mode
from FAKE_AGENT_MODE:

- ok: exit 0
- fail: exit 1
- hang: write its pid to FAKE_AGENT_OUT, then sleep for ten minutes
- spawn: start a child that sleeps, write the child's pid to FAKE_AGENT_OUT, then hang
- spawn_stubborn: the same, but the child ignores SIGTERM
- spawn_exit: start a child that sleeps, write its pid, then exit 0 leaving it behind
- ignore: ignore SIGTERM, write its pid, then hang (to exercise SIGKILL)
- names: write the sorted *names* (never values) it was given to FAKE_AGENT_OUT
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def _write(path: str | None, text: str) -> None:
    if path:
        with open(path, "w") as f:
            f.write(text)


def main() -> int:
    mode = os.environ.get("FAKE_AGENT_MODE", "ok")
    out = os.environ.get("FAKE_AGENT_OUT")
    if mode == "ok":
        return 0
    if mode == "fail":
        return 1
    if mode == "names":
        _write(out, "\n".join(sorted(os.environ)))
        return 0
    if mode == "spawn_exit":
        # Leave a child behind and exit successfully: the launcher must clean it up.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        _write(out, str(child.pid))
        return 0
    if mode in ("spawn", "spawn_stubborn"):
        # spawn_stubborn: the child ignores SIGTERM, so only the group SIGKILL ends it.
        ignore = "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        code = (ignore if mode == "spawn_stubborn" else "") + "import time; time.sleep(600)"
        child = subprocess.Popen([sys.executable, "-c", code])
        _write(out, str(child.pid))
    else:
        if mode == "ignore":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        _write(out, str(os.getpid()))
    time.sleep(600)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""nightpanel native messaging host.

Firefox launches this script when the nightpanel-bridge extension calls
connectNative('nightpanel'). It stays alive for the duration of the
browser session, watching np-command.json for changes written by the
orchestrator and forwarding them to the extension via stdout.

Protocol: each message = 4-byte little-endian uint32 length + UTF-8 JSON.
"""

from __future__ import annotations

import json
import os
import select
import struct
import sys
from pathlib import Path

POLL_INTERVAL = 0.5  # seconds


def _command_file() -> Path:
    """Resolved per call, not at import. Standalone script, so it cannot use Paths."""
    base = os.environ.get("NP_CONFIG_DIR") or (Path.home() / ".config" / "nightpanel")
    return Path(base) / "np-command.json"


def _read() -> dict | None:
    """Read one native-messaging message from stdin. Returns None on EOF."""
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    length = struct.unpack("<I", raw)[0]
    data = sys.stdin.buffer.read(length)
    try:
        return json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _write(msg: dict) -> None:
    """Send one native-messaging message to stdout (→ extension)."""
    data = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main() -> None:
    last_mtime: float | None = None

    while True:
        # Check for messages from the extension (non-blocking, 0.5 s timeout)
        ready, _, _ = select.select([sys.stdin.buffer], [], [], POLL_INTERVAL)
        if ready:
            msg = _read()
            if msg is None:
                break  # stdin closed — extension disconnected or Firefox quit
            # Extension → host messages (status pings etc.) — no-op for now

        # Watch for commands written by the orchestrator
        command_file = _command_file()
        if command_file.exists():
            try:
                mtime = command_file.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    command = json.loads(command_file.read_text())
                    _write(command)
            except (OSError, json.JSONDecodeError):
                pass


if __name__ == "__main__":
    main()

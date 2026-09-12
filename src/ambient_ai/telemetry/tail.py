"""Follow the telemetry line file from another terminal: python -m ambient_ai.telemetry.tail [path]

The path defaults to TELEMETRY_LOG_FILE. Lines are already redacted where they were built.
"""

from __future__ import annotations

import sys
import time

from ambient_ai.settings import env


def follow(path: str, *, poll_seconds: float = 0.25) -> None:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            print(line, end="", flush=True)
        while True:
            line = fh.readline()
            if line:
                print(line, end="", flush=True)
            else:
                time.sleep(poll_seconds)


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else env("TELEMETRY_LOG_FILE")
    if not path:
        print("usage: python -m ambient_ai.telemetry.tail <path>  (or set TELEMETRY_LOG_FILE)")
        return 2
    try:
        follow(path)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

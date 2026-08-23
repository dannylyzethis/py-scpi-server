"""Run pytest while publishing a useful GitHub Actions failure annotation."""

from __future__ import annotations

import subprocess
import sys
from collections import deque


def main() -> int:
    tail: deque[str] = deque(maxlen=120)
    process = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        tail.append(line)
    return_code = process.wait()
    if return_code:
        message = "".join(tail).strip()
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=pytest failure::{message}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

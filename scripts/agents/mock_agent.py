#!/usr/bin/env python3
from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    instance_id = str(payload.get("instance_id", "unknown"))

    response = {
        "patch": "",
        "metadata": {
            "agent": "mock",
            "instance_id": instance_id,
            "note": "Returns empty patch for pipeline validation.",
        },
    }
    sys.stdout.write(json.dumps(response))


if __name__ == "__main__":
    main()

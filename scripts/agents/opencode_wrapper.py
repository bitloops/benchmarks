#!/usr/bin/env python3
from benchkit.swebench.agents.opencode import wrapper as _impl

globals().update(
    {
        name: getattr(_impl, name)
        for name in dir(_impl)
        if not name.startswith("__")
    }
)


if __name__ == "__main__":
    _impl.main()

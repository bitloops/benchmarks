#!/usr/bin/env python3
"""Compatibility shim for tests/tools that import scripts/agents/common.py.

This re-exports the full benchkit implementation module, including private
underscore-prefixed helpers that some tests patch directly.
"""

from benchkit.swebench.agents import common as _common

for _name, _value in vars(_common).items():
    if _name.startswith("__") and _name.endswith("__"):
        continue
    globals()[_name] = _value

del _common
del _name
del _value

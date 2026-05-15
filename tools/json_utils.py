"""Backward-compat shim. Implementation lives in clang_tool_chain_bins._impl.json_utils."""

import sys as _sys

from clang_tool_chain_bins._impl import json_utils as _module

_sys.modules[__name__] = _module

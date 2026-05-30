"""Backward-compat shim. Implementation lives in clang_tool_chain_bins._impl.query."""

import sys as _sys

from clang_tool_chain_bins._impl import query as _module

_sys.modules[__name__] = _module

"""Backward-compat shim. Implementation lives in clang_tool_chain_bins._impl.archive_index."""

import sys as _sys

from clang_tool_chain_bins._impl import archive_index as _module

_sys.modules[__name__] = _module

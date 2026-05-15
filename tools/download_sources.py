"""Backward-compat shim. Implementation lives in clang_tool_chain_bins._impl.download_sources."""

import sys as _sys

from clang_tool_chain_bins._impl import download_sources as _module

_sys.modules[__name__] = _module

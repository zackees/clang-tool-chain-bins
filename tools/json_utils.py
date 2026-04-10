from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TextIO

import json5

if TYPE_CHECKING:
    from pathlib import Path


def loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json5.loads(text)


def load(handle: TextIO) -> Any:
    return loads(handle.read())


def load_path(path: Path) -> Any:
    return loads(path.read_text(encoding="utf-8"))

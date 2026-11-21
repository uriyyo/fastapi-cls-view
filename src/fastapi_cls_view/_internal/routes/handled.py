from __future__ import annotations

from typing import Any, Final
from weakref import WeakKeyDictionary

from starlette.requests import HTTPConnection

# an error a classmethod hook answered, in the request scope: a teardown still sees it
HANDLED_ERROR: Final = "fastapi_cls_view.handled_error"

# an error a method hook answered, by its view: the view is all the route and its teardown share
_HANDLED: WeakKeyDictionary[Any, Exception] = WeakKeyDictionary()


def record_handled(view: Any, exc: Exception, /) -> None:
    _HANDLED[view] = exc


def handled_error(view: Any, connection: HTTPConnection, /) -> Exception | None:
    return _HANDLED.get(view) or connection.scope.get(HANDLED_ERROR)


__all__ = [
    "HANDLED_ERROR",
    "handled_error",
    "record_handled",
]

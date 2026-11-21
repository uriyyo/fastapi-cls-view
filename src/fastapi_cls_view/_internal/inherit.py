from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from fastapi_cls_view.errors import OverrideError

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


def collect_inherited[T: DataclassInstance](
    cls: type[Any],
    kind: type[T],
    override: Callable[[T, object], T | None],
    /,
) -> dict[str, T]:
    # each name as Python looks it up - the first class in the MRO wins, mixins and diamonds too;
    # a plain value over an inherited item goes through `override`, and `None` drops the item
    mro = cls.__mro__[:-1]
    names = dict.fromkeys(name for base in reversed(mro) for name in vars(base))
    items: dict[str, T] = {}

    for name in names:
        values = [vars(base)[name] for base in mro if name in vars(base)]

        match values:
            case [value, *_] if isinstance(value, kind):
                items[name] = replace(value, name=name)
            case [value, *rest]:
                if (below := next((item for item in rest if isinstance(item, kind)), None)) is None:
                    continue

                if (item := override(replace(below, name=name), value)) is not None:
                    items[name] = item

    return items


def overrider[T: DataclassInstance](
    kind: str,
    attr: str,
    accepted: tuple[type[Any], ...],
    /,
) -> Callable[[T, object], T | None]:
    def override(item: T, value: object, /) -> T | None:
        match value:
            case None:
                return None
            case _ if isinstance(value, accepted):
                return replace(item, **{attr: value})
            case _:
                raise OverrideError(kind, getattr(item, "name", "?"), value)

    return override


__all__ = [
    "collect_inherited",
    "overrider",
]

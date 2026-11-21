from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, cast

from fastapi_cls_view.errors import UnknownViewConfigError
from fastapi_cls_view.types import ViewConfig

VIEW_CONFIG_KEYS: Final = frozenset(ViewConfig.__annotations__)


def pop_view_config(kwargs: dict[str, Any], /) -> ViewConfig:
    return cast(
        "ViewConfig",
        {key: kwargs.pop(key) for key in [*kwargs] if key in VIEW_CONFIG_KEYS},
    )


def merge_view_config(
    bases: Sequence[type[Any]],
    namespace: Mapping[str, Any] | None,
    kwargs: ViewConfig,
    /,
) -> ViewConfig:
    config: dict[str, Any] = {}

    # the first base wins, as for routes and hooks
    for base in reversed(bases):
        config.update(getattr(base, "__view_config__", {}))

    if namespace:
        if unknown := {*namespace} - VIEW_CONFIG_KEYS:
            raise UnknownViewConfigError(unknown)

        config.update(namespace)

    config.update(kwargs)
    return cast("ViewConfig", config)


__all__ = [
    "VIEW_CONFIG_KEYS",
    "merge_view_config",
    "pop_view_config",
]

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .views import View


class ViewError(Exception):
    pass


class DecoratorUsageError(ViewError, TypeError):
    pass


class ReservedNameError(ViewError, TypeError):
    def __init__(self, view: type[View], name: str, /) -> None:
        super().__init__(
            f"{view.__qualname__}.{name} would hide the view's own {name!r} - "
            f"use another name, or `view_config = ViewConfig(...)` without an annotation for the config",
        )

        self.name = name


class UnknownViewConfigError(ViewError, TypeError):
    def __init__(self, keys: set[str], /) -> None:
        super().__init__(f"unknown view config keys: {sorted(keys)}")

        self.keys = keys


class UnknownClassKeywordsError(ViewError, TypeError):
    def __init__(self, view: type[View], keywords: set[str], /) -> None:
        super().__init__(
            f"{view.__qualname__} got unknown class keywords {sorted(keywords)} - "
            f"a base view takes its own in `__view_init_subclass__(cls, **kwargs)`",
        )

        self.keywords = keywords


class ViewNotReadyError(ViewError, NameError):
    def __init__(self, view: type[View], error: NameError, /) -> None:
        super().__init__(
            f"{view.__qualname__} is not fully defined: {error} - "
            f"define it, then call {view.__qualname__}.view_rebuild(), passing it if it is not a module global",
        )

        self.view = view
        self.error = error
        self.name = error.name


class MissingSelfError(ViewError, TypeError):
    def __init__(self, func: Callable[..., Any], /) -> None:
        super().__init__(f"{func!r} must take the view instance as its first parameter")


class UnboundRouteError(ViewError, TypeError):
    def __init__(self, name: str, /) -> None:
        super().__init__(f"route {name!r} is not bound to a view - read it from the view class, not the decorator")


class UnknownHookRouteError(ViewError, LookupError):
    def __init__(self, hook: str, names: set[str], /) -> None:
        super().__init__(f"hook {hook!r} names routes the view does not have: {sorted(names)}")


class UnknownRouteError(ViewError, LookupError):
    def __init__(self, view: type[View], names: set[str], /) -> None:
        super().__init__(f"{view.__qualname__} has no routes {sorted(names)}")

        self.names = names


class UnknownFieldError(ViewError, TypeError):
    def __init__(self, view: type[View], names: set[str], /) -> None:
        super().__init__(f"{view.__qualname__} has no fields {sorted(names)}")

        self.names = names


class UnknownDependencyError(ViewError, LookupError):
    def __init__(self, view: type[View], name: str, /) -> None:
        super().__init__(f"{view.__qualname__} has no dependency {name!r}")

        self.name = name


class NotAViewRouteError(ViewError, LookupError):
    def __init__(self, path: str, /) -> None:
        super().__init__(f"{path!r} is not served by a view route - `CurrentRoute` only works in one")


class ViewDependencyCycleError(ViewError, TypeError):
    def __init__(self, view: type[View], field: str, /) -> None:
        super().__init__(
            f"{view.__qualname__}.{field} depends on a `@dependency` of the view itself, which needs the view - "
            f"use it in a route or hook instead",
        )


class UnsupportedViewOptionError(ViewError, TypeError):
    def __init__(self, view: type[View], options: set[str], /) -> None:
        super().__init__(f"{view.__qualname__} can't use the dataclass options {sorted(options)}")

        self.options = options


class OverrideError(ViewError, TypeError):
    def __init__(self, kind: str, name: str, value: object, /) -> None:
        super().__init__(
            f"{name} overrides an inherited {kind} with {value!r} - "
            f"override it with a function, or set it to `None` to drop it",
        )


class UnsupportedHookError(ViewError, TypeError):
    def __init__(self, hook: str, route: str, reason: str, /) -> None:
        super().__init__(f"hook {hook!r} can't run on route {route!r}: {reason}")


class StreamHookError(ViewError, TypeError):
    def __init__(self, hook: Callable[..., Any], kind: str, /) -> None:
        super().__init__(f"{hook!r} wraps a streaming route, so it must be {kind} generator function")


__all__ = [
    "DecoratorUsageError",
    "MissingSelfError",
    "NotAViewRouteError",
    "OverrideError",
    "ReservedNameError",
    "StreamHookError",
    "UnboundRouteError",
    "UnknownClassKeywordsError",
    "UnknownDependencyError",
    "UnknownFieldError",
    "UnknownHookRouteError",
    "UnknownRouteError",
    "UnknownViewConfigError",
    "UnsupportedHookError",
    "UnsupportedViewOptionError",
    "ViewDependencyCycleError",
    "ViewError",
    "ViewNotReadyError",
]

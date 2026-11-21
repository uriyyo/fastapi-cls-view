from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, final

from fastapi_cls_view.docs import Documented
from fastapi_cls_view.types import (
    Hookable,
    HookMode,
    ResponseDocs,
)


# one info per decorator, as pydantic's `FieldValidatorDecoratorInfo` and the like
@final
@dataclass(frozen=True, slots=True)
class RequestHookInfo:
    mode: HookMode
    routes: tuple[str, ...]  # empty: every route


@final
@dataclass(frozen=True, slots=True)
class ErrorHookInfo:
    routes: tuple[str, ...]
    exceptions: tuple[type[BaseException], ...]
    excluded: tuple[type[BaseException], ...] = ()

    def distance(self, exc: BaseException, /) -> int | None:
        if isinstance(exc, self.excluded):
            return None

        mro = type(exc).__mro__
        return min((mro.index(tp) for tp in self.exceptions if tp in mro), default=None)


@final
@dataclass(frozen=True, slots=True)
class LifespanHookInfo:
    pass  # runs with the app, not on a route


type HookInfo = RequestHookInfo | ErrorHookInfo | LifespanHookInfo


@final
@dataclass(frozen=True, slots=True)
class ViewHook[I: HookInfo](Documented):
    name: str
    wrapped: Hookable
    info: I
    docs: ResponseDocs = ()

    @property
    def func(self) -> Callable[..., Any]:
        match self.wrapped:
            case classmethod(__func__=func):
                return func
            case func:
                return func

    def applies_to(self, route: str, /) -> bool:
        match self.info:
            case LifespanHookInfo():
                return False
            case info:
                return not info.routes or route in info.routes

    @property
    def on_class(self) -> bool:
        return isinstance(self.wrapped, classmethod)

    def __get__(self, instance: Any, owner: type[Any] | None = None, /) -> Any:
        return self.wrapped.__get__(instance, owner)


__all__ = [
    "ErrorHookInfo",
    "HookInfo",
    "LifespanHookInfo",
    "RequestHookInfo",
    "ViewHook",
]

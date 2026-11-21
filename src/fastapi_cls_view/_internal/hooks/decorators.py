from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from types import FunctionType
from typing import Any, Final, get_args, overload

from fastapi.exceptions import RequestValidationError, WebSocketRequestValidationError
from starlette.exceptions import HTTPException
from typing_extensions import TypeIs

from fastapi_cls_view.errors import DecoratorUsageError
from fastapi_cls_view.types import (
    ExceptionTypes,
    Hookable,
    HookMode,
)

from .info import ErrorHookInfo, HookInfo, LifespanHookInfo, RequestHookInfo, ViewHook

_MODES: Final = frozenset(get_args(HookMode.__value__))


def _method_kind(info: HookInfo, /) -> str:
    match info:
        # before the view is built, or without it
        case RequestHookInfo(mode="request") | LifespanHookInfo():
            return "class"
        # a classmethod handles what building the view raises too, a method only the route's own errors
        case ErrorHookInfo():
            return "any"
        case _:
            return "instance"


def _hook[I: HookInfo](target: Any, info: I, /) -> ViewHook[I]:
    match target:
        case ViewHook():
            raise DecoratorUsageError(f"{target.func!r} is already a hook - a method is one hook")
        case FunctionType() if _method_kind(info) == "class":
            raise DecoratorUsageError(f"{target!r} runs before the view is built: make it a `@classmethod`")
        case classmethod(__func__=func) if _method_kind(info) == "instance":
            raise DecoratorUsageError(f"{func!r} gets the view instance: make it a plain method")
        case FunctionType(__name__=name) | classmethod(__func__=FunctionType(__name__=name)):
            return ViewHook(name, target, info)
        case _:
            raise DecoratorUsageError(f"a hook decorates a method or classmethod of a view, got {target!r}")


def _is_exception_type(target: object, /) -> TypeIs[type[BaseException]]:
    return isinstance(target, type) and issubclass(target, BaseException)


def _is_target(arg: object, /) -> bool:
    return isinstance(arg, str) or _is_exception_type(arg)


def _routes(routes: Sequence[str], /) -> tuple[str, ...]:
    if not all(isinstance(route, str) for route in routes):
        raise DecoratorUsageError(f"a hook names its routes by their method names, got {routes!r}")

    # `"*"` is every route, as in pydantic's `field_validator("*")`
    if "*" in routes:
        return ()

    return tuple(routes)


def _decorator[I: HookInfo](info: I, /) -> Callable[[Any], ViewHook[I]]:
    def decorator(target: Any, /) -> ViewHook[I]:
        return _hook(target, info)

    return decorator


@overload
def request_hook[F: Hookable](func: F, /) -> F: ...


@overload
def request_hook[F: Hookable](*routes: str, mode: HookMode = "after") -> Callable[[F], F]: ...


def request_hook(*args: Any, mode: HookMode = "after") -> Any:
    if mode not in _MODES:
        raise DecoratorUsageError(f"unknown hook mode {mode!r}, expected one of {sorted(_MODES)}")

    match args:
        case (target,) if not _is_target(target):
            return _hook(target, RequestHookInfo(mode, ()))
        case _ if any(_is_exception_type(arg) for arg in args):
            raise DecoratorUsageError("exceptions are handled by `@error_hook(...)`, not `request_hook()`")
        case _:
            return _decorator(RequestHookInfo(mode, _routes(args)))


def _is_exceptions(target: object, /) -> TypeIs[ExceptionTypes]:
    # as an `except` clause takes them: one type or a non-empty tuple
    match target:
        case tuple():
            return bool(target) and all(_is_exception_type(tp) for tp in target)
        case _:
            return _is_exception_type(target)


# what a hook catches unless it names its exceptions: FastAPI's own errors already answer as they should
_NOT_BY_DEFAULT: Final = (HTTPException, RequestValidationError, WebSocketRequestValidationError)


def _error_info(exc: object, routes: tuple[Any, ...], /) -> ErrorHookInfo:
    if exc is None:
        return ErrorHookInfo(_routes(routes), (Exception,), _NOT_BY_DEFAULT)

    if not _is_exceptions(exc):
        raise DecoratorUsageError(f"an error hook handles an exception type or a tuple of them, got {exc!r}")

    return ErrorHookInfo(_routes(routes), exc if isinstance(exc, tuple) else (exc,))


@overload
def error_hook[F: Hookable](func: F, /) -> F: ...


@overload
def error_hook[F: Hookable](exc: ExceptionTypes, /, *routes: str) -> Callable[[F], F]: ...


@overload
def error_hook[F: Hookable](*routes: str, exc: ExceptionTypes = ...) -> Callable[[F], F]: ...


def error_hook(*args: Any, exc: ExceptionTypes | None = None) -> Any:
    match args:
        case (target,) if not isinstance(target, (str, tuple)) and not _is_exception_type(target):
            return _hook(target, _error_info(exc, ()))
        case (first, *routes) if not isinstance(first, str):
            if exc is not None:
                raise DecoratorUsageError("an error hook takes its exceptions first or as `exc=`, not both")

            return _decorator(_error_info(first, tuple(routes)))
        case routes:
            return _decorator(_error_info(exc, routes))


def lifespan[F: classmethod[Any, ..., Any]](func: F, /) -> F:
    """A classmethod async generator run with the app: `yield` once, between startup and shutdown."""

    if not inspect.isasyncgenfunction(getattr(func, "__func__", None)):
        raise DecoratorUsageError(f"lifespan() decorates an async generator classmethod, got {func!r}")

    return _hook(func, LifespanHookInfo())  # ty: ignore[invalid-explicit-return]


__all__ = [
    "error_hook",
    "lifespan",
    "request_hook",
]

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from types import FunctionType
from typing import TYPE_CHECKING, Any

from typing_extensions import TypeIs

from fastapi_cls_view._internal.inherit import collect_inherited, overrider
from fastapi_cls_view.errors import UnknownHookRouteError, UnsupportedHookError
from fastapi_cls_view.types import (
    AnyViewHook,
    AnyViewRoute,
    ErrorHook,
    HookMode,
    LifespanHook,
    RequestHook,
    ViewHooks,
)

from .info import ErrorHookInfo, HookInfo, LifespanHookInfo, RequestHookInfo, ViewHook
from .stream import is_stream

if TYPE_CHECKING:
    from fastapi_cls_view.views import View


def view_lifespan(view: type[View], hooks: list[LifespanHook], /) -> Callable[[Any], Any]:
    # each in order, the bases' first; shut down in reverse
    @asynccontextmanager
    async def view_lifespan(app: Any) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            for hook in hooks:
                await stack.enter_async_context(asynccontextmanager(hook.func)(view, app))

            yield

    return view_lifespan


def _is_hook[I: HookInfo](hook: AnyViewHook, kind: type[I], /) -> TypeIs[ViewHook[I]]:
    return isinstance(hook.info, kind)


def lifespan_hooks(hooks: list[AnyViewHook], /) -> list[LifespanHook]:
    return [hook for hook in hooks if _is_hook(hook, LifespanHookInfo)]


def request_hooks(hooks: list[AnyViewHook], /, *modes: HookMode) -> list[RequestHook]:
    return [hook for hook in hooks if _is_hook(hook, RequestHookInfo) and hook.info.mode in modes]


def error_hooks(hooks: list[AnyViewHook], /, *, on_class: bool | None = None) -> list[ErrorHook]:
    return [hook for hook in hooks if _is_hook(hook, ErrorHookInfo) and on_class in {None, hook.on_class}]


def closest_error_hook(hooks: list[ErrorHook], exc: Exception, /) -> ErrorHook | None:
    # the closest exception type wins; on a tie, the one defined last - a subclass's
    found: ErrorHook | None = None
    closest: int | None = None

    for hook in hooks:
        distance = hook.info.distance(exc)

        if distance is None:
            continue

        if closest is None or distance <= closest:
            found, closest = hook, distance

    return found


_override_hook = overrider("hook", "wrapped", (FunctionType, classmethod))


def collect_hooks(view: type[View], /) -> ViewHooks:
    return collect_inherited(view, ViewHook, _override_hook)


def _unsupported(hook: AnyViewHook, route: AnyViewRoute, /) -> str | None:
    match hook.info:
        case RequestHookInfo(mode="before" | "wrap" | "teardown"):
            return None
        case _ if route.is_websocket:
            return "a websocket route only runs `before`, `wrap` and `teardown` hooks"
        case ErrorHookInfo() if not hook.on_class and is_stream(route.func):
            return "a stream has started its response when it fails - use a `@classmethod` error hook"
        case _:
            return None


def check_hook_routes(view: type[View], /) -> None:
    routes = view.__view_routes__
    # a base's hook may name a route a subclass dropped, `users = None`
    known = {name for base in view.__mro__ for name in base.__dict__.get("__view_routes__", {})}

    for hook in view.__view_hooks__.values():
        if isinstance(hook.info, LifespanHookInfo):
            continue

        if unknown := {*hook.info.routes} - known:
            raise UnknownHookRouteError(hook.name, unknown)

        # named explicitly: a `"*"` hook just skips the routes it can't run on
        for name in {*hook.info.routes} & {*routes}:
            if (reason := _unsupported(hook, routes[name])) is not None:
                raise UnsupportedHookError(hook.name, name, reason)


__all__ = [
    "check_hook_routes",
    "closest_error_hook",
    "collect_hooks",
    "error_hooks",
    "lifespan_hooks",
    "request_hooks",
    "view_lifespan",
]

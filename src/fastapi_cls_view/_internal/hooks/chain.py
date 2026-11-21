from __future__ import annotations

from collections.abc import Callable
from functools import partial, wraps
from typing import TYPE_CHECKING, Any

from fastapi_injected.ext import NotAsyncError, is_async_callable

from fastapi_cls_view._internal.routes.binding import run, split_view
from fastapi_cls_view._internal.routes.handled import record_handled
from fastapi_cls_view.types import Handler

from .collect import closest_error_hook

if TYPE_CHECKING:
    from fastapi_cls_view.types import ErrorHook, RequestHook


def _after(hook: Callable[..., Any], view: Any, handler: Handler[Any], /) -> Any:
    return hook(view, handler())


async def _async_after(hook: Callable[..., Any], view: Any, handler: Handler[Any], /) -> Any:
    return await run(hook, view, await handler())


type _After = Callable[[Callable[..., Any], Any, Handler[Any]], Any]


def _layer(hook: RequestHook, view: Any, handler: Handler[Any], after: _After, /) -> Handler[Any]:
    match hook.info.mode:
        case "wrap":
            return partial(hook.func, view, handler)
        case _:
            return partial(after, hook.func, view, handler)


def _stack(hooks: list[RequestHook], view: Any, handler: Handler[Any], after: _After, /) -> Handler[Any]:
    # as pydantic applies validators: each hook wraps the ones defined before it
    for hook in hooks:
        handler = _layer(hook, view, handler, after)

    return handler


def _method_hook(errors: list[ErrorHook], view: Any, exc: Exception, /) -> ErrorHook | None:
    # one hook wins over both kinds: a closer classmethod one answers in the route class
    if (hook := closest_error_hook(errors, exc)) is None or hook.on_class:
        return None

    record_handled(view, exc)
    return hook


def _handle(errors: list[ErrorHook], view: Any, exc: Exception, /) -> Any:
    # `None`: no hook answered, the error goes on
    return None if (hook := _method_hook(errors, view, exc)) is None else hook.func(view, exc)


async def _async_handle(errors: list[ErrorHook], view: Any, exc: Exception, /) -> Any:
    return None if (hook := _method_hook(errors, view, exc)) is None else await run(hook.func, view, exc)


def hooked(func: Callable[..., Any], self_name: str, hooks: list[RequestHook], errors: list[ErrorHook], /) -> Any:
    if not any(is_async_callable(f) for f in (func, *(hook.func for hook in (*hooks, *errors)))):

        @wraps(func)
        def call(*args: Any, **kwargs: Any) -> Any:
            view, args = split_view(self_name, args, kwargs)

            try:
                return _stack(hooks, view, partial(func, view, *args, **kwargs), _after)()
            except Exception as exc:
                if (response := _handle(errors, view, exc)) is None:
                    raise

                return response

        return call

    for hook in hooks:
        if hook.info.mode == "wrap" and not is_async_callable(hook.func):
            raise NotAsyncError(hook.func)

    @wraps(func)
    async def async_call(*args: Any, **kwargs: Any) -> Any:
        view, args = split_view(self_name, args, kwargs)

        try:
            return await _stack(hooks, view, partial(run, func, view, *args, **kwargs), _async_after)()
        except Exception as exc:
            if (response := await _async_handle(errors, view, exc)) is None:
                raise

            return response

    return async_call


__all__ = [
    "hooked",
]

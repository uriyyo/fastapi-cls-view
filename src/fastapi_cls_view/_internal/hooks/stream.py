from __future__ import annotations

import inspect
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import aclosing, closing
from functools import WRAPPER_ASSIGNMENTS, partial
from typing import TYPE_CHECKING, Any

from fastapi_injected.ext import is_async_callable
from starlette.concurrency import iterate_in_threadpool

from fastapi_cls_view._internal.routes.binding import run, split_view
from fastapi_cls_view.errors import StreamHookError

if TYPE_CHECKING:
    from fastapi_cls_view.types import RequestHook


def is_stream(func: Callable[..., Any], /) -> bool:
    return inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func)


def _map(hook: Callable[..., Any], view: Any, items: Any, /) -> Generator[Any]:
    with closing(items):
        for item in items:
            yield hook(view, item)


async def _async_map(hook: Callable[..., Any], view: Any, items: Any, /) -> AsyncGenerator[Any]:
    async with aclosing(items):
        async for item in items:
            yield await run(hook, view, item)


def _returning(value: Any, /) -> Any:
    return value


def _makes_stream_async(hook: RequestHook, /) -> bool:
    match hook.info.mode:
        case "after":
            return is_async_callable(hook.func)
        case _:
            return inspect.isasyncgenfunction(hook.func)


def _check_stream_wrap(hook: RequestHook, is_async: bool, /) -> None:
    if hook.info.mode != "wrap":
        return

    if is_async and not inspect.isasyncgenfunction(hook.func):
        raise StreamHookError(hook.func, "an async")

    if not is_async and not inspect.isgeneratorfunction(hook.func):
        raise StreamHookError(hook.func, "a")


def _named_after[F: Callable[..., Any]](wrapper: F, func: Callable[..., Any], /) -> F:
    # not `wraps`: FastAPI looks through `__wrapped__` to tell how to stream, and the route may be of another kind
    for attr in WRAPPER_ASSIGNMENTS:
        setattr(wrapper, attr, getattr(func, attr))

    return wrapper


def _wrap_items(hooks: list[RequestHook], view: Any, items: Any, is_async: bool, made: list[Any], /) -> Any:
    for hook in hooks:
        if hook.info.mode == "wrap":
            items = hook.func(view, partial(_returning, items))
        elif is_async:
            items = _async_map(hook.func, view, items)
        else:
            items = _map(hook.func, view, items)

        made.append(items)

    return items


async def _aclose_all(made: list[Any], /) -> None:
    # a `wrap` hook iterates what it was handed, `async for` never closes it: the outermost first
    for items in reversed(made):
        if (aclose := getattr(items, "aclose", None)) is not None:
            await aclose()
        elif (close := getattr(items, "close", None)) is not None:
            close()


def _close_all(made: list[Any], /) -> None:
    for items in reversed(made):
        if (close := getattr(items, "close", None)) is not None:
            close()


def streamed(func: Callable[..., Any], self_name: str, hooks: list[RequestHook], /) -> Any:
    is_async = inspect.isasyncgenfunction(func) or any(_makes_stream_async(hook) for hook in hooks)

    for hook in hooks:
        _check_stream_wrap(hook, is_async)

    def stream(view: Any, args: tuple[Any, ...], kwargs: dict[str, Any], made: list[Any]) -> Any:
        items: Any = func(view, *args, **kwargs)
        made.append(items)

        if is_async and inspect.isgeneratorfunction(func):
            items = iterate_in_threadpool(items)

        return _wrap_items(hooks, view, items, is_async, made)

    if is_async:

        async def async_call(*args: Any, **kwargs: Any) -> AsyncGenerator[Any]:
            view, args = split_view(self_name, args, kwargs)
            made: list[Any] = []

            try:
                async for item in stream(view, args, kwargs, made):
                    yield item
            finally:
                await _aclose_all(made)

        return _named_after(async_call, func)

    def call(*args: Any, **kwargs: Any) -> Generator[Any]:
        view, args = split_view(self_name, args, kwargs)
        made: list[Any] = []

        try:
            yield from stream(view, args, kwargs, made)
        finally:
            _close_all(made)

    return _named_after(call, func)


__all__ = [
    "is_stream",
    "streamed",
]

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import cache
from typing import Any, override

from fastapi import Request, Response
from fastapi.routing import APIRoute

from fastapi_cls_view._internal.hooks.collect import closest_error_hook
from fastapi_cls_view.types import AnyViewRoute

from .binding import run
from .handled import HANDLED_ERROR


class _HookedRoute(APIRoute):
    @override
    def __init__(self, *args: Any, view_route: AnyViewRoute | None = None, **kwargs: Any) -> None:
        # set first: `APIRoute.__init__` builds the handler
        self.view_route = view_route
        super().__init__(*args, **kwargs)

    @override
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        route = self.view_route

        if route is None:
            return handler

        view = route.view
        requests = route.request_hooks("request")
        errors = route.error_hooks

        async def hooked(request: Request) -> Response:
            try:
                for hook in reversed(requests):
                    if (response := await run(hook.func, view, request)) is not None:
                        return response

                return await handler(request)
            except Exception as exc:
                if (hook := closest_error_hook(errors, exc)) is None:
                    raise

                request.scope[HANDLED_ERROR] = exc

                if (response := await run(hook.func, view, request, exc)) is None:
                    raise

                return response

        return hooked


@cache
def hooked_route_class(base: type[APIRoute], /) -> type[APIRoute]:
    if issubclass(base, _HookedRoute):
        return base

    return type(f"Hooked{base.__name__}", (_HookedRoute, base), {})


__all__ = [
    "hooked_route_class",
]

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from types import FunctionType
from typing import TYPE_CHECKING, Any, Unpack, cast, final

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse

from fastapi_cls_view._internal.routes.route import ViewRoute
from fastapi_cls_view._internal.view.markers import bind_markers

from .docs import merge_responses
from .errors import DecoratorUsageError
from .types import AnyViewRoute, APIRouteOptions, RouteDecorator, RouteMethod, RouteOptions, WebSocketOptions


@final
@dataclass(frozen=True, slots=True)
class RouteInfo:
    method: RouteMethod
    params: dict[str, Any]  # only what was passed explicitly

    @property
    def is_websocket(self) -> bool:
        return self.method == "websocket"

    @property
    def http_methods(self) -> list[str]:
        match self.method:
            case "api_route":
                return [*(self.params.get("methods") or ["GET"])]
            case method:
                return [method.upper()]

    def apply(self, router: APIRouter, route: AnyViewRoute, /) -> None:
        view = route.view
        params: dict[str, Any] = {**self.params}

        if params.get("name") is None:
            params["name"] = route.route_name

        if route.hook_dependencies or params.get("dependencies"):
            params["dependencies"] = [*route.hook_dependencies, *bind_markers(params.get("dependencies") or (), view)]

        if self.is_websocket:
            router.websocket(**params)(route.endpoint)
            return

        if responses := route.responses:
            params["responses"] = merge_responses([responses, params.get("responses") or {}])

        route_class = route.route_class_override(router.route_class)

        methods = self.http_methods

        # one route per method, so each gets its own operation id
        for http_method in methods:
            method_params: dict[str, Any] = {**params, "methods": [http_method]}

            match method_params.get("operation_id"):
                case None:
                    method_params["operation_id"] = view.__view_operation_id__(route, http_method, params["path"])
                case operation_id if len(methods) > 1:
                    method_params["operation_id"] = f"{operation_id}_{http_method.lower()}"

            router.add_api_route(endpoint=route.endpoint, route_class_override=route_class, **method_params)


def _route_decorator(name: RouteMethod, /) -> Callable[..., RouteDecorator]:
    method = getattr(APIRouter, name)
    sign = inspect.signature(method)

    def route_decorator(*args: Any, **kwargs: Any) -> RouteDecorator:
        # fails where the decorator is written, not in `as_router()`
        params = sign.bind(None, *args, **kwargs).arguments
        params.pop("self")
        info = RouteInfo(name, params)

        def decorator(func: Any, /) -> AnyViewRoute:
            match func:
                case ViewRoute() if func.is_websocket != info.is_websocket:
                    raise DecoratorUsageError(f"{func.name} is either a websocket route or an HTTP one, not both")
                case ViewRoute():
                    # the outermost decorator runs last: it goes first, in source order
                    return replace(func, infos=(info, *func.infos))
                case FunctionType():
                    return ViewRoute(func.__name__, func, (info,))
                case _:
                    raise DecoratorUsageError(f"{name}() decorates a method of a view, got {func!r}")

        return cast("RouteDecorator", decorator)

    route_decorator.__name__ = route_decorator.__qualname__ = name
    route_decorator.__doc__ = method.__doc__

    return route_decorator


if TYPE_CHECKING:

    def get(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...
    def post(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...
    def put(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...
    def patch(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...
    def delete(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...
    def options(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...
    def head(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...
    def trace(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...
    def api_route(path: str, **options: Unpack[APIRouteOptions]) -> RouteDecorator: ...
    def websocket(path: str, **options: Unpack[WebSocketOptions]) -> RouteDecorator: ...
    def sse(path: str, **options: Unpack[RouteOptions]) -> RouteDecorator: ...

else:
    get = _route_decorator("get")
    post = _route_decorator("post")
    put = _route_decorator("put")
    patch = _route_decorator("patch")
    delete = _route_decorator("delete")
    options = _route_decorator("options")
    head = _route_decorator("head")
    trace = _route_decorator("trace")
    api_route = _route_decorator("api_route")
    websocket = _route_decorator("websocket")

    def sse(path: str, **options: Any) -> RouteDecorator:
        """`get()` for a stream of server-sent events: yield models, values or `ServerSentEvent`s."""

        if "response_class" in options:
            raise DecoratorUsageError("sse() always responds with `EventSourceResponse`")

        return get(path, response_class=EventSourceResponse, **options)


__all__ = [
    "RouteInfo",
    "api_route",
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "sse",
    "trace",
    "websocket",
]

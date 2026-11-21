from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, final
from weakref import WeakKeyDictionary

from fastapi import Depends
from starlette.requests import HTTPConnection

from fastapi_cls_view.errors import NotAViewRouteError
from fastapi_cls_view.types import AnyViewRoute

if TYPE_CHECKING:
    from fastapi_cls_view.views import View

_ROUTES: WeakKeyDictionary[Callable[..., Any], AnyViewRoute] = WeakKeyDictionary()


@final
@dataclass(frozen=True, slots=True)
class RouteContext:
    route: AnyViewRoute
    method: str  # `WEBSOCKET` for a websocket

    @property
    def name(self) -> str:
        return self.route.name

    @property
    def view(self) -> type[View]:
        return self.route.view


def register_endpoint(endpoint: Callable[..., Any], route: AnyViewRoute, /) -> None:
    _ROUTES[endpoint] = route


def current_route(connection: HTTPConnection) -> RouteContext:
    endpoint = getattr(connection.scope.get("route"), "endpoint", None)

    if endpoint is None or (route := _ROUTES.get(endpoint)) is None:
        raise NotAViewRouteError(connection.scope.get("path", ""))

    return RouteContext(route, connection.scope.get("method", "WEBSOCKET"))


type CurrentRoute = Annotated[RouteContext, Depends(current_route)]


__all__ = [
    "CurrentRoute",
    "RouteContext",
    "current_route",
    "register_endpoint",
]

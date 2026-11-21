from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, replace
from functools import cached_property, partial
from types import FunctionType
from typing import TYPE_CHECKING, Any, Concatenate, cast, final

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, params
from fastapi.routing import APIRoute
from starlette.datastructures import URL
from starlette.requests import HTTPConnection

from fastapi_cls_view._internal.hooks.chain import hooked
from fastapi_cls_view._internal.hooks.collect import error_hooks, request_hooks
from fastapi_cls_view._internal.hooks.stream import is_stream, streamed
from fastapi_cls_view._internal.inherit import collect_inherited, overrider
from fastapi_cls_view._internal.member import ViewMember, bind_members
from fastapi_cls_view._internal.view.generics import substitute, typevars_for
from fastapi_cls_view.docs import Documented, ResponseDoc, merge_responses
from fastapi_cls_view.types import (
    AnyViewHook,
    AnyViewRoute,
    ErrorHook,
    HookMode,
    RequestHook,
    ResponseDocs,
    Responses,
    ViewRoutes,
)

from .binding import bind_view, run
from .context import register_endpoint
from .handled import handled_error
from .route_class import hooked_route_class

if TYPE_CHECKING:
    from fastapi_cls_view.decorators import RouteInfo
    from fastapi_cls_view.views import View


@final
@dataclass(frozen=True, eq=False)
class ViewRoute[S, **P, R](ViewMember[S, P, R], Documented):
    """A route of a view: what `UsersView.users` is, while `view.users` stays the bound method."""

    collected_in = "__view_routes__"

    name: str
    func: Callable[Concatenate[S, P], R]
    infos: tuple[RouteInfo, ...]
    docs: ResponseDocs = ()
    owner: type[View] | None = None

    @property
    def route_name(self) -> str:
        for info in self.infos:
            if (name := info.params.get("name")) is not None:
                return name

        return self.view.__view_route_name__(self)

    def url_path_for(self, app: FastAPI | APIRouter, /, **path_params: Any) -> str:
        return str(app.url_path_for(self.route_name, **path_params))

    def url_for(self, connection: HTTPConnection, /, **path_params: Any) -> URL:
        """The absolute URL, as the request sees the app - `root_path` and proxies included."""

        return connection.url_for(self.route_name, **path_params)

    @property
    def is_websocket(self) -> bool:
        return any(info.is_websocket for info in self.infos)

    @property
    def hooks(self) -> list[AnyViewHook]:
        return [hook for hook in self.view.__view_hooks__.values() if hook.applies_to(self.name)]

    def request_hooks(self, *modes: HookMode) -> list[RequestHook]:
        return request_hooks(self.hooks, *modes)

    @property
    def error_hooks(self) -> list[ErrorHook]:
        # classmethods: around the whole request, in the route class
        return error_hooks(self.hooks, on_class=True)

    @property
    def instance_error_hooks(self) -> list[ErrorHook]:
        # methods: around the route, with its view
        return error_hooks(self.hooks, on_class=False)

    @property
    def responses(self) -> Responses:
        # the route wins over its hooks; the view's own docs go to its router
        docs = [
            *(self._typed(doc, hook.func) for hook in self.hooks for doc in hook.docs),
            *(self._typed(doc, self.func) for doc in self.docs),
        ]
        return merge_responses(doc.to_fastapi() for doc in docs)

    def _typed(self, doc: ResponseDoc, func: Callable[..., Any], /) -> ResponseDoc:
        # `responds(409, model=M)` in a generic view: as its routes are
        if doc.model is None:
            return doc

        return replace(doc, model=substitute(doc.model, typevars_for(self.view, func)))

    @cached_property
    def endpoint(self) -> Callable[..., Any]:
        endpoint = _view_endpoint(self)
        register_endpoint(endpoint, self)
        return endpoint

    def route_class_override(self, base: type[APIRoute], /) -> type[APIRoute] | None:
        if not self.error_hooks and not self.request_hooks("request"):
            return None

        # FastAPI only ever calls it to build the route - so a partial carrying this route will do
        return cast("type[APIRoute]", partial(hooked_route_class(base), view_route=self))

    @cached_property
    def hook_dependencies(self) -> list[params.Depends]:
        # the last defined runs first, as pydantic's `before` validators - and its teardown too
        befores = reversed(self.request_hooks("before"))
        teardowns = self.request_hooks("teardown")
        # a websocket has no response to run them after
        backgrounds = [] if self.is_websocket else self.request_hooks("background")

        return [
            *(Depends(bind_view(hook.func, self.view).wrap(hook.func)) for hook in befores),
            *(_teardown(hook, self.view, stream=is_stream(self.func)) for hook in teardowns),
            *(_background(hook, self.view) for hook in backgrounds),
        ]

    def register(self, router: APIRouter, /) -> None:
        for info in self.infos:
            info.apply(router, self)


def _teardown(hook: RequestHook, view: type[View], /, *, stream: bool) -> params.Depends:
    # a default, not `Annotated`: this module's annotations are strings
    async def teardown(connection: HTTPConnection, instance: Any = Depends(view)) -> AsyncGenerator[None]:  # noqa: B008
        try:
            yield
        except Exception as exc:
            await run(hook.func, instance, exc)
            raise
        else:
            await run(hook.func, instance, handled_error(instance, connection))

    # a stream's route runs as the response is sent: the teardown waits for it
    return Depends(teardown, scope="request" if stream else "function")


def _background(hook: RequestHook, view: type[View], /) -> params.Depends:
    # queued up front, run once the response is sent - unless the request failed, an error hook's answer included
    def background(tasks: BackgroundTasks, connection: HTTPConnection, instance: Any = Depends(view)) -> None:  # noqa: B008
        async def task() -> None:
            if handled_error(instance, connection) is None:
                await run(hook.func, instance)

        tasks.add_task(task)

    return Depends(background)


def _view_endpoint(route: AnyViewRoute, /) -> Callable[..., Any]:
    sign = bind_view(route.func, route.view, include_return=True)
    self_name = next(iter(sign.parameters))

    # the rest run elsewhere: `before`, `teardown` and `background` as dependencies, `request` in the route class
    hooks = route.request_hooks("wrap") if route.is_websocket else route.request_hooks("after", "wrap")

    if is_stream(route.func):
        return sign.wrap(streamed(route.func, self_name, hooks)) if hooks else sign.wrap(route.func)

    # both kinds: the closest wins, wherever it runs
    errors = [] if route.is_websocket or not route.instance_error_hooks else error_hooks(route.hooks)

    if not hooks and not errors:
        return sign.wrap(route.func)

    return sign.wrap(hooked(route.func, self_name, hooks, errors))


_override_route = overrider("route", "func", (FunctionType,))


def collect_routes(view: type[View], /) -> ViewRoutes:
    return bind_members(view, collect_inherited(view, ViewRoute, _override_route))


__all__ = [
    "ViewRoute",
    "collect_routes",
]

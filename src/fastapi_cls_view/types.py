from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import Enum
from types import FunctionType
from typing import TYPE_CHECKING, Any, Concatenate, Literal, Protocol, TypeVar, overload, runtime_checkable

from fastapi import params
from fastapi.routing import APIRoute
from fastapi.types import IncEx
from starlette.responses import Response
from starlette.routing import BaseRoute
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from fastapi._form_limits import _FormLimits

    from fastapi_cls_view._internal.hooks.info import ErrorHookInfo, LifespanHookInfo, RequestHookInfo, ViewHook
    from fastapi_cls_view._internal.routes.route import ViewRoute
    from fastapi_cls_view._internal.view.fields import ViewField

    from .docs import ResponseDoc
    from .views import View

type HookMode = Literal["request", "before", "after", "wrap", "teardown", "background"]
type RouteMethod = Literal[
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "head",
    "trace",
    "api_route",
    "websocket",
]
type Handler[R] = Callable[[], R] | Callable[[], Awaitable[R]]
type ExceptionTypes = type[BaseException] | tuple[type[BaseException], ...]
type Hookable = FunctionType | classmethod[Any, ..., Any]
type Responses = dict[int | str, dict[str, Any]]
type TypeVarMap = dict[TypeVar, Any]
type AnyViewRoute = ViewRoute[Any, ..., Any]
type ViewRoutes = dict[str, AnyViewRoute]
type AnyViewHook = ViewHook[Any]
type RequestHook = ViewHook[RequestHookInfo]
type ErrorHook = ViewHook[ErrorHookInfo]
type LifespanHook = ViewHook[LifespanHookInfo]
type ViewHooks = dict[str, AnyViewHook]
type ViewFields = dict[str, ViewField]
type ResponseDocs = tuple[ResponseDoc, ...]
type Dependencies = Sequence[params.Depends]
type UniqueIdFunction = Callable[[APIRoute], str]


class RouteDecorator(Protocol):
    @overload
    def __call__[S, **P, R](self, func: ViewRoute[S, P, R], /) -> ViewRoute[S, P, R]: ...

    @overload
    def __call__[S, **P, R](self, func: Callable[Concatenate[S, P], R], /) -> ViewRoute[S, P, R]: ...


@runtime_checkable
class BindsToView(Protocol):
    @property
    def owner(self) -> type[View] | None: ...

    def dependency_for(self, view: type[View], /) -> Callable[..., Any]: ...


class ViewConfig(TypedDict, total=False):
    prefix: str
    tags: Sequence[str | Enum]
    dependencies: Dependencies
    default_response_class: type[Response]
    responses: Mapping[int | str, Mapping[str, Any]] | Sequence[ResponseDoc]
    callbacks: list[BaseRoute]
    redirect_slashes: bool
    route_class: type[APIRoute]
    deprecated: bool
    include_in_schema: bool
    generate_unique_id_function: UniqueIdFunction
    strict_content_type: bool
    max_body_size: int
    form_limits: _FormLimits


class _RouteOptions(TypedDict, total=False):
    response_model: Any
    status_code: int | None
    tags: list[str | Enum] | None
    dependencies: Dependencies | None
    summary: str | None
    description: str | None
    response_description: str
    responses: Responses | None
    deprecated: bool | None
    operation_id: str | None
    response_model_include: IncEx | None
    response_model_exclude: IncEx | None
    response_model_by_alias: bool
    response_model_exclude_unset: bool
    response_model_exclude_defaults: bool
    response_model_exclude_none: bool
    include_in_schema: bool
    response_class: type[Response]
    name: str | None
    callbacks: list[BaseRoute] | None
    openapi_extra: dict[str, Any] | None
    generate_unique_id_function: UniqueIdFunction
    max_body_size: int | None
    form_limits: _FormLimits | None


class RouteOptions(_RouteOptions, total=False, closed=True):
    pass


class APIRouteOptions(_RouteOptions, total=False, closed=True):
    methods: list[str] | None


class WebSocketOptions(TypedDict, total=False, closed=True):
    name: str | None
    dependencies: Dependencies | None


__all__ = [
    "APIRouteOptions",
    "AnyViewHook",
    "AnyViewRoute",
    "BindsToView",
    "Dependencies",
    "ErrorHook",
    "ExceptionTypes",
    "Handler",
    "HookMode",
    "Hookable",
    "LifespanHook",
    "RequestHook",
    "ResponseDocs",
    "Responses",
    "RouteDecorator",
    "RouteMethod",
    "RouteOptions",
    "TypeVarMap",
    "UniqueIdFunction",
    "ViewConfig",
    "ViewFields",
    "ViewHooks",
    "ViewRoutes",
    "WebSocketOptions",
]

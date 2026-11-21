import asyncio
import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, get_args

import pytest
from fastapi import APIRouter, Depends, FastAPI, Query, WebSocket, params
from fastapi.testclient import TestClient
from fastapi_injected import resolve
from pydantic import BaseModel

import fastapi_cls_view
from fastapi_cls_view import (
    MissingSelfError,
    OverrideError,
    ReservedNameError,
    UnknownClassKeywordsError,
    UnknownViewConfigError,
    View,
    ViewConfig,
    ViewNotReadyError,
    ViewRoute,
    api_route,
    get,
    post,
    websocket,
)
from fastapi_cls_view._internal.view.config import VIEW_CONFIG_KEYS
from fastapi_cls_view.types import APIRouteOptions, RouteMethod, RouteOptions, WebSocketOptions

if TYPE_CHECKING:
    from collections.abc import Sequence


class UsersService:
    def __init__(self, users: "list[dict[str, Any]] | None" = None) -> None:
        self.users = users or [{"id": 1, "name": "John", "password": "secret"}]

    def get_users(self) -> list[dict[str, Any]]:
        return self.users


class User(BaseModel):
    id: int
    name: str


def app_with(*views: type[View]) -> FastAPI:
    app = FastAPI()

    for view in views:
        app.include_router(view.as_router())

    return app


# 1. config: bases, then `view_config`, then class keywords


def test_config_merge() -> None:
    class Base(View, tags=["base"], prefix="/base"):
        pass

    class Users(Base, prefix="/users"):
        view_config = ViewConfig(tags=["users"], deprecated=True)

    class Admins(Users, deprecated=False):
        pass

    assert Base.__view_config__ == {"tags": ["base"], "prefix": "/base"}
    assert Users.__view_config__ == {
        "tags": ["users"],
        "prefix": "/users",
        "deprecated": True,
    }
    assert Admins.__view_config__ == {
        "tags": ["users"],
        "prefix": "/users",
        "deprecated": False,
    }


def test_config_in_router() -> None:
    class Users(View, prefix="/users", tags=["users"]):
        @get("/")
        def users(self) -> list[int]:
            return [1]

    app = app_with(Users)

    assert TestClient(app).get("/users/").json() == [1]
    assert app.openapi()["paths"]["/users/"]["get"]["tags"] == ["users"]

    # `as_router()` overrides win
    assert [getattr(route, "path", None) for route in Users.as_router(prefix="/v2").routes] == ["/v2/"]


def test_unknown_view_config() -> None:
    with pytest.raises(UnknownViewConfigError):

        class Users(View):
            view_config = {"prefixx": "/users"}  # type: ignore[ty:unknown-typed-dict-constructor-key]  # noqa: RUF012


# 2. route decorators: every APIRouter helper, stacked, bound early


def test_route_decorators() -> None:
    class Users(View, prefix="/users"):
        service: Annotated[UsersService, Depends()]

        @get("/", summary="List users", status_code=200)
        @get("/all", include_in_schema=False)
        def users(self, limit: int = 10) -> list[User]:
            return self.service.get_users()[:limit]  # type: ignore[ty:invalid-explicit-return]

        @post("/", status_code=201)
        async def create(self, name: str) -> dict[str, str]:
            return {"created": name}

        @websocket("/ws")
        async def ws(self, ws: WebSocket) -> None:
            await ws.accept()
            await ws.send_json(self.service.get_users()[0]["name"])
            await ws.close()

    client = TestClient(app := app_with(Users))

    # `-> list[User]` is the response model: the password is not returned
    assert client.get("/users/").json() == [{"id": 1, "name": "John"}]
    assert client.get("/users/all", params={"limit": 0}).json() == []
    assert client.post("/users/", params={"name": "bob"}).status_code == 201

    with client.websocket_connect("/users/ws") as ws:
        assert ws.receive_json() == "John"

    operation = app.openapi()["paths"]["/users/"]["get"]
    assert operation["summary"] == "List users"
    assert [param["name"] for param in operation["parameters"]] == ["limit"]
    assert "/users/all" not in app.openapi()["paths"]


def test_route_decorator_arguments_checked_where_written() -> None:
    with pytest.raises(TypeError):
        get("/", summry="typo")  # type: ignore[ty:unknown-argument]


def test_api_route_split_per_method() -> None:
    class Items(View):
        @api_route("/items", methods=["PUT", "PATCH"])
        def update(self) -> str:
            return "ok"

    app = app_with(Items)
    paths = app.openapi()["paths"]["/items"]

    assert TestClient(app).patch("/items").json() == "ok"
    assert paths["put"]["operationId"] != paths["patch"]["operationId"]


def test_missing_self() -> None:
    class Broken(View):
        @get("/")  # type: ignore[ty:no-matching-overload] - a route without `self` fails type checking too
        def nope() -> None:
            pass

    with pytest.raises(MissingSelfError):
        Broken.as_router()


# 3. lazy build: annotations may name what is defined after the view


def test_forward_reference() -> None:
    class Later(View):
        service: Annotated["LaterService", Depends()]

        @get("/")
        def get_value(self) -> int:
            return self.service.value

    with pytest.raises(ViewNotReadyError):
        Later.as_router()

    @dataclass
    class LaterService:
        value: int = 42

    Later.view_rebuild(LaterService=LaterService)
    assert TestClient(app_with(Later)).get("/").json() == 42


def test_forward_reference_in_route() -> None:
    class Item(BaseModel):
        id: int

    class Items(View):
        @get("/")
        def items(self, limit: "Annotated[int, Query()]" = 1) -> "list[Later]":
            return [{"item": Item(id=limit), "secret": "x"}]  # type: ignore[ty:invalid-explicit-return]

    class Later(BaseModel):
        item: Item

    Items.view_rebuild(Later=Later)
    assert TestClient(app_with(Items)).get("/", params={"limit": 2}).json() == [{"item": {"id": 2}}]


def test_type_checking_only_annotation() -> None:
    def get_names() -> list[str]:
        return ["a", "b"]

    class Names(View):
        names: "Sequence[str]" = Depends(get_names)

        @get("/")
        def all_names(self) -> list[str]:
            return [*self.names]

    assert TestClient(app_with(Names)).get("/").json() == ["a", "b"]


# 4. the view signature: `Depends(View)` and `resolve()` go through it


def test_view_signature() -> None:
    class Users(View):
        service: Annotated[UsersService, Depends()]
        other: UsersService = Depends()
        tenant: Annotated[str, Query(alias="t")] = "default"
        page: int = Query(1)
        cache: dict[str, Any] = field(default_factory=dict)
        _private: int = 0
        counter: ClassVar[int] = 0

    sign = inspect.signature(Users)

    assert [*sign.parameters] == ["service", "other", "tenant", "page"]
    assert all(param.kind is inspect.Parameter.KEYWORD_ONLY for param in sign.parameters.values())
    page = sign.parameters["page"].default
    assert isinstance(page, params.Query)
    assert page.default == 1

    view = Users(service=UsersService(), other=UsersService())
    assert view._private == 0  # noqa: SLF001
    assert view.cache == {}


def test_request_params_on_view() -> None:
    class Tenants(View):
        tenant: Annotated[str, Query(alias="t")] = "default"

        @get("/a")
        def a(self) -> str:
            return self.tenant

        @get("/b")
        async def b(self, extra: int = 0) -> str:
            return f"{self.tenant}:{extra}"

    client = TestClient(app_with(Tenants))

    assert client.get("/a").json() == "default"
    assert client.get("/a", params={"t": "acme"}).json() == "acme"
    assert client.get("/b", params={"t": "acme", "extra": 1}).json() == "acme:1"


def test_depends_on_view_and_resolve() -> None:
    class Users(View):
        service: Annotated[UsersService, Depends()]

    app = FastAPI()

    @app.get("/")
    def names(users: Annotated[Users, Depends()]) -> list[str]:
        return [user["name"] for user in users.service.get_users()]

    assert TestClient(app).get("/").json() == ["John"]

    view = asyncio.run(resolve(Users))
    assert isinstance(view, Users)
    assert isinstance(view.service, UsersService)


def test_dependency_overrides() -> None:
    class Users(View):
        service: Annotated[UsersService, Depends()]

        @get("/")
        def users(self) -> list[dict[str, Any]]:
            return self.service.get_users()

    app = app_with(Users)
    app.dependency_overrides[UsersService] = lambda: UsersService(users=[{"id": 2}])

    assert TestClient(app).get("/").json() == [{"id": 2}]


# 5. inheritance: routes by method name, along the MRO


def test_route_inheritance() -> None:
    class Base(View):
        @get("/hello")
        def hello(self) -> str:
            return "base"

        @get("/bye")
        def bye(self) -> str:
            return "bye"

    class Child(Base):
        # overridden without a decorator: the route stays, with the new implementation
        def hello(self) -> str:
            return "child"

        # anything else drops it
        bye = None  # type: ignore[assignment]

    client = TestClient(app_with(Child))

    assert [*Child.__view_routes__] == ["hello"]
    assert client.get("/hello").json() == "child"
    assert client.get("/bye").status_code == 404
    assert TestClient(app_with(Base)).get("/hello").json() == "base"


def test_mixin_routes() -> None:
    class HealthMixin:
        @get("/health")
        def health(self) -> str:
            return "ok"

    class Users(HealthMixin, View):
        pass

    assert TestClient(app_with(Users)).get("/health").json() == "ok"


# 6. class access: route info on the class, the bound method on an instance


def test_class_level_route() -> None:
    class Users(View, prefix="/users"):
        @get("/{user_id}")
        def user(self, user_id: int) -> int:
            return user_id

    app = app_with(Users)

    assert isinstance(Users.user, ViewRoute)
    assert Users.user.view is Users
    assert Users.user.url_path_for(app, user_id=1) == "/users/1"
    assert Users().user(5) == 5


# 7. hooks


def test_hooks() -> None:
    class Named(View):
        @classmethod
        def __view_route_name__(cls, route: ViewRoute[Any, ..., Any], /) -> str:
            return f"{cls.__name__}.{route.name}"

        @classmethod
        def __view_operation_id__(cls, route: ViewRoute[Any, ..., Any], method: str, path: str, /) -> str:
            return f"{cls.__name__.lower()}_{route.name}_{method.lower()}"

        @get("/")
        def users(self) -> list[int]:
            return []

    app = app_with(Named)

    assert Named.users.url_path_for(app) == "/"
    assert app.url_path_for("Named.users") == "/"
    assert app.openapi()["paths"]["/"]["get"]["operationId"] == "named_users_get"


def test_view_init_subclass_hook() -> None:
    seen: list[str] = []

    class Tracked(View):
        @classmethod
        def __view_init_subclass__(cls, *, audit: bool = False, **kwargs: Any) -> None:
            super().__view_init_subclass__(**kwargs)
            seen.append(f"{cls.__name__}:{[*cls.__view_routes__]}:{audit}")

    class Users(Tracked, audit=True):
        @get("/")
        def users(self) -> None:
            pass

    # like `__init_subclass__`: the hook of a class runs for its subclasses, with their own class keywords
    assert seen == ["Users:['users']:True"]

    with pytest.raises(UnknownClassKeywordsError):

        class Typo(Tracked, audti=True):
            pass


def test_view_on_complete_runs_once_the_fields_resolve() -> None:
    completed: list[str] = []

    class Later(View):
        value: Annotated[int, Query()]

        @classmethod
        def __view_on_complete__(cls) -> None:
            completed.append(cls.__name__)

    assert completed == []

    Later.as_router()
    Later.view_rebuild()

    assert completed == ["Later"]


def test_reserved_names() -> None:
    with pytest.raises(ReservedNameError):

        class Shadow(View):
            as_router: int = 1  # type: ignore[ty:incompatible-attribute-override]

    with pytest.raises(ReservedNameError):

        class AnnotatedConfig(View):
            view_config: ViewConfig = ViewConfig(prefix="/x")  # type: ignore[ty:incompatible-attribute-override]

    class Fine(View):
        view_config = ViewConfig(prefix="/fine")

    assert Fine.__view_config__ == {"prefix": "/fine"}


def test_a_generic_view_in_a_function_sees_its_locals() -> None:
    Local = int  # noqa: N806 - the name a string annotation in the view uses

    class Box[T](View):
        value: Annotated["Local", Query()]

        @get("/")
        def read(self) -> T:
            return self.value  # type: ignore[ty:invalid-explicit-return]

    class IntBox(Box[int]):
        pass

    assert TestClient(app_with(IntBox)).get("/", params={"value": 3}).json() == 3


def test_view_config_covers_the_router_options() -> None:
    # what a view has no use for: it is a router of its own routes, included into an app
    not_for_views = {"routes", "default", "dependency_overrides_provider", "on_startup", "on_shutdown", "lifespan"}

    assert {*inspect.signature(APIRouter).parameters} - not_for_views == VIEW_CONFIG_KEYS


def test_route_options_match_the_router() -> None:
    def options_of(method: Any) -> set[str]:
        return {*inspect.signature(method).parameters} - {"self", "path"}

    assert {*RouteOptions.__annotations__} == options_of(APIRouter.get)
    assert {*APIRouteOptions.__annotations__} == options_of(APIRouter.api_route)
    assert {*WebSocketOptions.__annotations__} == options_of(APIRouter.websocket)


def test_route_methods_are_router_helpers() -> None:
    for name in get_args(RouteMethod.__value__):
        assert callable(getattr(APIRouter, name))
        assert callable(getattr(fastapi_cls_view, name))


# regressions


def test_url_path_for_follows_the_route_name() -> None:
    class Users(View):
        @get("/", name="list_users")
        def users(self) -> int:
            return 1  # pragma: no cover

    app = app_with(Users)

    assert Users.users.route_name == "list_users"
    assert Users.users.url_path_for(app) == "/"


def test_the_first_base_wins_the_config() -> None:
    class A(View, prefix="/a"):
        pass

    class B(View, prefix="/b", tags=["b"]):
        pass

    class C(A, B):
        @get("/")
        def r(self) -> int:
            return 1  # pragma: no cover

    assert C.__view_config__ == {"prefix": "/a", "tags": ["b"]}


def test_a_route_is_callable_from_the_class() -> None:
    class Users(View):
        @get("/")
        def users(self, page: int = 1) -> int:
            return page

    class Child(Users):
        def users(self, page: int = 1) -> int:
            return Users.users(self, page) * 10

    assert Child().users(2) == 20


def test_only_none_drops_an_inherited_route() -> None:
    class Users(View):
        @get("/")
        def users(self) -> int:
            return 1  # pragma: no cover

    with pytest.raises(OverrideError):

        class Child(Users):
            users = 1


def test_a_concrete_generic_view_is_a_subclass() -> None:
    class Echo[T](View):
        @get("/")
        def echo(self, value: T) -> T:
            return value

    assert Echo[int] is Echo[int]
    assert issubclass(Echo[int], Echo)
    assert TestClient(app_with(Echo[int])).get("/", params={"value": "7"}).json() == 7


def test_an_inherited_route_sees_its_own_scope() -> None:
    def make() -> type[View]:
        class Local(BaseModel):
            id: int

        class Base(View):
            @get("/")
            def item(self) -> "Local":
                return Local(id=1)

        return Base

    class Child(make()):  # ty: ignore[unsupported-base]
        pass

    assert TestClient(app_with(Child)).get("/").json() == {"id": 1}


def test_an_api_route_with_an_operation_id_keeps_them_unique() -> None:
    class Users(View):
        @api_route("/", methods=["GET", "POST"], operation_id="users")
        def users(self) -> int:
            return 1  # pragma: no cover

    paths = app_with(Users).openapi()["paths"]["/"]

    assert {paths["get"]["operationId"], paths["post"]["operationId"]} == {"users_get", "users_post"}

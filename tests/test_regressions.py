import asyncio
from collections.abc import AsyncIterator
from dataclasses import field
from typing import Annotated, Any, Generic, TypeVar

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from fastapi_cls_view import (
    Handler,
    UnsupportedViewOptionError,
    View,
    ViewDependencyCycleError,
    ViewNotReadyError,
    dependency,
    error_hook,
    get,
    request_hook,
    responds,
)


def client_for(*views: type[View]) -> TestClient:
    app = FastAPI()

    for view in views:
        app.include_router(view.as_router())

    return TestClient(app)


class Users(View):
    @dependency
    def user(self, user_id: int = 1) -> str:
        return f"user{user_id}"

    @dependency
    def check(self, blocked: bool = False) -> None:
        if blocked:
            raise HTTPException(403)


# `@dependency` wherever FastAPI takes a dependency


def test_a_dependency_in_another_views_field_and_dependencies_lists() -> None:
    class Posts(View, prefix="/posts", dependencies=[Depends(Users.check)]):
        user: Annotated[str, Depends(Users.user)]

        @get("/", dependencies=[Depends(Users.check)])
        def posts(self) -> str:
            return self.user

    client = client_for(Posts)

    assert client.get("/posts/", params={"user_id": 2}).json() == "user2"
    assert client.get("/posts/", params={"blocked": True}).status_code == 403


def test_a_dependency_outside_a_view_says_what_to_use() -> None:
    app = FastAPI()

    with pytest.raises(TypeError, match=r"Users\.user\.dependency"):

        @app.get("/")
        def plain(user: Annotated[str, Depends(Users.user)]) -> str:
            return user  # pragma: no cover

    @app.get("/ok")
    def ok(user: Annotated[str, Depends(Users.user.dependency)]) -> str:
        return user

    assert TestClient(app).get("/ok").json() == "user1"


def test_a_field_cant_use_its_own_views_dependency() -> None:
    class Own(View):
        @dependency
        def value(self) -> int:
            return 1  # pragma: no cover

        other: Annotated[int, Depends(value)]

        @get("/")
        def read(self) -> int:
            return self.other  # pragma: no cover

    with pytest.raises(ViewDependencyCycleError):
        Own.as_router()


def test_an_inherited_dependency_is_the_subclasss() -> None:
    built: list[str] = []

    class Base(View):
        def __post_init__(self) -> None:
            built.append(type(self).__name__)

        @dependency
        def user(self) -> str:
            return "base"

    class Child(Base):
        def user(self) -> str:
            return "child"

        @get("/")
        def read(self, user: Annotated[str, Depends(Base.user)]) -> str:
            return user

    assert client_for(Child).get("/").json() == "child"
    assert built == ["Child"]


# generics


class Repo[T](View):
    def describe(self) -> str:
        return type(self).__name__


def test_a_generic_field_typed_as_a_generic_view_is_injected() -> None:
    class Service[T](View):
        repo: Repo[T]

        @get("/")
        def read(self) -> str:
            return self.repo.describe()

    assert client_for(Service[int]).get("/").json() == "Repo[int]"


T = TypeVar("T")


class Box(View, Generic[T]):  # noqa: UP046 - one `TypeVar` on two levels, as only the old syntax allows
    @get("/")
    def one(self) -> T:
        return [1]  # type: ignore[ty:invalid-explicit-return]


class Boxes(Box[list[T]], Generic[T]):  # noqa: UP046
    pass


def test_one_type_variable_on_two_levels() -> None:
    app = FastAPI()
    app.include_router(Boxes[int].as_router())

    schema = app.openapi()["paths"]["/"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert schema["type"] == "array"
    assert TestClient(app).get("/").json() == [1]


class Item(BaseModel):
    id: int


def test_responds_models_are_substituted_too() -> None:
    class Crud[M](View):
        @responds(409, model=M)
        @get("/")
        def read(self) -> M:
            return Item(id=1)  # type: ignore[ty:invalid-explicit-return]

    app = FastAPI()
    app.include_router(Crud[Item].as_router())
    conflict = app.openapi()["paths"]["/"]["get"]["responses"]["409"]["content"]["application/json"]["schema"]

    assert conflict == {"$ref": "#/components/schemas/Item"}


def test_parametrized_views_have_their_own_names() -> None:
    class Crud[M](View):
        pass

    assert Crud[list[int]].__name__ == "Crud[list[int]]"
    assert Crud[list[int]] is not Crud[list[str]]
    assert Crud[int | None].__name__ == "Crud[int | None]"


# inheritance, as Python resolves it


def test_a_mixin_and_a_diamond_resolve_as_python_does() -> None:
    class A(View):
        @get("/r")
        def r(self) -> str:
            return "A"

    class Mixin:
        def r(self) -> str:
            return "Mixin"

    class E(Mixin, A):
        pass

    class B(A):
        r = None

    class C(A):
        pass

    class D(B, C):
        pass

    assert client_for(E).get("/r").json() == "Mixin"
    assert "r" not in D.__view_routes__


# streams close what they made


def test_an_async_stream_with_a_wrap_hook_closes_the_route() -> None:
    log: list[str] = []

    class Feed(View):
        @request_hook(mode="wrap")
        async def framed(self, handler: Handler[Any]) -> AsyncIterator[int]:
            async for item in handler():  # type: ignore[ty:not-iterable]
                yield item

        @get("/")
        async def feed(self) -> AsyncIterator[int]:
            try:
                for item in range(10):
                    yield item
            finally:
                log.append("closed")

    async def first_then_close() -> None:
        items = Feed.feed.endpoint(Feed())
        assert await anext(items) == 0
        await items.aclose()
        assert log == ["closed"]

    asyncio.run(first_then_close())


# teardown and errors


def test_a_teardown_sees_what_a_method_error_hook_answered() -> None:
    seen: list[Any] = []

    class Users(View):
        @request_hook(mode="teardown")
        def done(self, exc: Exception | None) -> None:
            seen.append(type(exc).__name__)

        @error_hook(KeyError)
        def missing(self, exc: Exception) -> Response:
            return PlainTextResponse("missing")

        @get("/")
        def fail(self) -> int:
            raise KeyError("x")

    assert client_for(Users).get("/").text == "missing"
    assert seen == ["KeyError"]


def test_a_bare_error_hook_leaves_fastapis_own_errors_alone() -> None:
    class Users(View):
        @error_hook
        @classmethod
        def anything(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse("caught", status_code=500)

        @get("/")
        def page(self, number: int = 0) -> int:
            if number == 404:
                raise HTTPException(404)

            raise ValueError

    client = client_for(Users)

    assert client.get("/", params={"number": 404}).status_code == 404
    assert client.get("/", params={"number": "x"}).status_code == 422
    assert client.get("/").text == "caught"


def test_the_closest_error_hook_wins_across_both_kinds() -> None:
    class Users(View):
        @error_hook(Exception)
        def broad(self, exc: Exception) -> Response:
            return PlainTextResponse("method")

        @error_hook(ValueError)
        @classmethod
        def exact(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse("class")

        @get("/")
        def fail(self) -> int:
            raise ValueError

    assert client_for(Users).get("/").text == "class"


def test_a_hook_may_name_a_route_a_subclass_dropped() -> None:
    class Base(View):
        @request_hook("users")
        def shout(self, result: str) -> str:
            return result.upper()  # pragma: no cover

        @get("/users")
        def users(self) -> str:
            return "x"  # pragma: no cover

        @get("/other")
        def other(self) -> str:
            return "other"

    class Child(Base):
        users = None

    assert client_for(Child).get("/other").json() == "other"


# fail early, not per request


def test_an_unresolved_route_annotation_fails_the_router() -> None:
    class Users(View):
        @get("/")
        def read(self, q: "Missing") -> int:  # type: ignore[ty:unresolved-reference]  # noqa: F821
            return 1  # pragma: no cover

    with pytest.raises(ViewNotReadyError, match="Missing"):
        Users.as_router()


def test_slots_are_refused() -> None:
    with pytest.raises(UnsupportedViewOptionError):

        class Users(View, slots=True):
            pass


# names and overrides


def test_route_names_include_the_view() -> None:
    class Users(View, prefix="/users"):
        @get("/")
        def list(self) -> int:
            return 1  # pragma: no cover

    class Items(View, prefix="/items"):
        @get("/")
        def list(self) -> int:
            return 1  # pragma: no cover

    app = FastAPI()
    app.include_router(Users.as_router())
    app.include_router(Items.as_router())

    assert Users.list.url_path_for(app) == "/users/"
    assert Items.list.url_path_for(app) == "/items/"
    assert Users.list.route_name.endswith("Users.list")


def test_a_route_has_one_name_across_its_decorators() -> None:
    class Users(View):
        @get("/users", name="users")
        @get("/people")
        def list(self) -> int:
            return 1  # pragma: no cover

    router = Users.as_router()

    assert [route.name for route in router.routes] == ["users", "users"]  # type: ignore[ty:missing-attribute]
    assert Users.list.url_path_for(router) == "/users"


def test_override_takes_factory_fields() -> None:
    class Users(View):
        cache: dict[str, int] = field(default_factory=dict)

        @get("/")
        def read(self) -> dict[str, int]:
            return self.cache

    app = FastAPI()
    app.include_router(Users.as_router())

    with Users.override(app, cache={"fake": 1}):
        assert TestClient(app).get("/").json() == {"fake": 1}

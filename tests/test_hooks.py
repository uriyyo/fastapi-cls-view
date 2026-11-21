from collections.abc import AsyncIterator, Callable, Iterator
from functools import wraps
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute
from fastapi.sse import EventSourceResponse
from fastapi.testclient import TestClient
from fastapi_injected.ext import NotAsyncError
from pydantic import BaseModel

from fastapi_cls_view import (
    Handler,
    StreamHookError,
    UnknownHookRouteError,
    UnsupportedHookError,
    View,
    error_hook,
    get,
    request_hook,
    websocket,
)


def client_for(*views: type[View]) -> TestClient:
    app = FastAPI()

    for view in views:
        app.include_router(view.as_router())

    return TestClient(app)


def test_before_hook_runs_with_its_own_params_and_the_routes_view() -> None:
    class Users(View):
        _tenant: str = ""

        @request_hook(mode="before")
        def read_tenant(self, x_tenant: Annotated[str, Header()]) -> None:
            if x_tenant == "blocked":
                raise HTTPException(403)

            self._tenant = x_tenant

        @get("/")
        def users(self) -> str:
            return self._tenant

    client = client_for(Users)

    assert client.get("/", headers={"x-tenant": "acme"}).json() == "acme"
    assert client.get("/", headers={"x-tenant": "blocked"}).status_code == 403
    assert client.get("/").status_code == 422

    [param] = client.app.openapi()["paths"]["/"]["get"]["parameters"]  # type: ignore[ty:missing-attribute]
    assert param["name"] == "x-tenant"


def test_after_hook_transforms_the_result() -> None:
    class Users(View):
        @request_hook("users", mode="after")
        def hide(self, result: list[str]) -> list[str]:
            return [name for name in result if not name.startswith("_")]

        @get("/")
        def users(self) -> list[str]:
            return ["a", "_b"]

        @get("/raw")
        def raw(self) -> list[str]:
            return ["a", "_b"]

    client = client_for(Users)

    assert client.get("/").json() == ["a"]
    assert client.get("/raw").json() == ["a", "_b"]


def test_a_bare_hook_is_an_after_hook_for_every_route() -> None:
    class Users(View):
        @request_hook
        def shout(self, result: str) -> str:
            return result.upper()

        @get("/")
        def users(self) -> str:
            return "a"

    assert client_for(Users).get("/").json() == "A"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_wrap_hook_calls_the_handler(kind: str) -> None:
    def sync_wrap(self: Any, handler: Handler[str]) -> str:
        try:
            return f"<{handler()}>"
        except ValueError:
            return "caught"

    async def async_wrap(self: Any, handler: Handler[str]) -> str:
        try:
            return f"<{await handler()}>"  # type: ignore[ty:invalid-await]
        except ValueError:
            return "caught"

    class Users(View):
        around = request_hook(mode="wrap")(sync_wrap if kind == "sync" else async_wrap)

        @get("/")
        def users(self) -> str:
            return "a"

        @get("/fail")
        def fail(self) -> str:
            raise ValueError

    client = client_for(Users)

    assert client.get("/").json() == "<a>"
    assert client.get("/fail").json() == "caught"


def test_hooks_wrap_the_ones_defined_before_them() -> None:
    log: list[str] = []

    class Users(View):
        @request_hook(mode="before")
        def first_before(self) -> None:
            log.append("first_before")

        @request_hook(mode="before")
        def second_before(self) -> None:
            log.append("second_before")

        @request_hook(mode="after")
        def first_after(self, result: list[str]) -> list[str]:
            return [*result, "first_after"]

        @request_hook(mode="wrap")
        async def around(self, handler: Handler[list[str]]) -> list[str]:
            log.append("wrap in")
            result = await handler()  # type: ignore[ty:invalid-await]
            log.append("wrap out")
            return [*result, "wrap"]

        @request_hook(mode="after")
        def second_after(self, result: list[str]) -> list[str]:
            return [*result, "second_after"]

        @get("/")
        def users(self) -> list[str]:
            log.append("route")
            return ["route"]

    assert client_for(Users).get("/").json() == ["route", "first_after", "wrap", "second_after"]
    # as pydantic's `before` validators: the last defined runs first
    assert log == ["second_before", "first_before", "wrap in", "route", "wrap out"]


def test_subclass_hooks_wrap_the_inherited_ones() -> None:
    class Base(View):
        @request_hook
        def base(self, result: list[str]) -> list[str]:
            return [*result, "base"]

        @request_hook
        def renamed(self, result: list[str]) -> list[str]:
            return [*result, "renamed"]  # pragma: no cover

        @request_hook
        def dropped(self, result: list[str]) -> list[str]:
            return [*result, "dropped"]  # pragma: no cover

        @get("/")
        def items(self) -> list[str]:
            return []

    class Child(Base):
        @request_hook
        def child(self, result: list[str]) -> list[str]:
            return [*result, "child"]

        # overridden without the decorator: the hook stays where it was, with the new body
        def renamed(self, result: list[str]) -> list[str]:
            return [*result, "overridden"]

        dropped = None  # type: ignore[assignment]

    assert client_for(Child).get("/").json() == ["base", "overridden", "child"]
    assert client_for(Base).get("/").json() == ["base", "renamed", "dropped"]


def test_hooks_of_a_mixin() -> None:
    class Audited:
        @request_hook
        def audit(self, result: str) -> str:
            return f"{result}!"

    class Users(Audited, View):
        @get("/")
        def users(self) -> str:
            return "a"

    assert client_for(Users).get("/").json() == "a!"


def test_a_sync_route_with_async_hooks() -> None:
    class Users(View):
        @request_hook
        async def after(self, result: str) -> str:
            return f"{result}!"

        @get("/")
        def users(self) -> str:
            return "a"

    assert client_for(Users).get("/").json() == "a!"


def test_a_sync_wrap_hook_cannot_await_an_async_chain() -> None:
    class Users(View):
        @request_hook(mode="wrap")
        def around(self, handler: Handler[str]) -> str:
            return handler()  # type: ignore[ty:invalid-explicit-return]  # pragma: no cover

        @get("/")
        async def users(self) -> str:
            return "a"  # pragma: no cover

    with pytest.raises(NotAsyncError):
        Users.as_router()


def test_websocket_routes_skip_after_hooks() -> None:
    seen: list[str] = []

    class Live(View):
        @request_hook(mode="before")
        def connect(self) -> None:
            seen.append("before")

        @request_hook
        def after(self, result: Any) -> Any:
            seen.append("after")  # pragma: no cover
            return result  # pragma: no cover

        @websocket("/ws")
        async def ws(self, ws: WebSocket) -> None:
            await ws.accept()
            await ws.send_text("hi")
            await ws.close()

    with client_for(Live).websocket_connect("/ws") as ws:
        assert ws.receive_text() == "hi"

    assert seen == ["before"]


def test_a_hook_naming_an_unknown_route() -> None:
    class Users(View):
        @request_hook("missing")
        def after(self, result: str) -> str:
            return result  # pragma: no cover

        @get("/")
        def users(self) -> str:
            return "a"  # pragma: no cover

    with pytest.raises(UnknownHookRouteError):
        Users.as_router()


def test_a_function_is_one_hook() -> None:
    with pytest.raises(TypeError, match="already a hook"):

        @request_hook(mode="before")
        @request_hook
        def twice(self: Any, result: Any) -> Any:
            return result  # pragma: no cover


class Item(BaseModel):
    id: int


def test_after_hooks_run_on_each_streamed_item() -> None:
    class Items(View):
        @request_hook
        def times_ten(self, item: Item) -> Item:
            return Item(id=item.id * 10)

        @request_hook("mixed")
        async def plus_one(self, item: Item) -> Item:
            return Item(id=item.id + 1)

        @get("/sync")
        def sync_items(self) -> Iterator[Item]:
            yield Item(id=1)
            yield Item(id=2)

        @get("/async")
        async def async_items(self) -> AsyncIterator[Item]:
            yield Item(id=1)

        @get("/mixed")
        def mixed(self) -> Iterator[Item]:
            yield Item(id=1)

        @get("/sse", response_class=EventSourceResponse)
        async def sse(self) -> AsyncIterator[Item]:
            yield Item(id=3)

    client = client_for(Items)

    assert client.get("/sync").text == '{"id":10}\n{"id":20}\n'
    assert client.get("/async").text == '{"id":10}\n'
    assert client.get("/mixed").text == '{"id":11}\n'
    assert client.get("/sse").text == 'data: {"id":30}\n\n'

    schema = client.app.openapi()["paths"]["/async"]["get"]["responses"]["200"]["content"]  # type: ignore[ty:missing-attribute]
    assert schema["application/jsonl"]["itemSchema"] == {"$ref": "#/components/schemas/Item"}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_a_wrap_hook_of_a_stream_is_a_generator(kind: str) -> None:
    def sync_guard(self: Any, handler: Callable[[], Iterator[Item]]) -> Iterator[Item]:
        try:
            yield from handler()
        except RuntimeError:
            yield Item(id=-1)

    async def async_guard(self: Any, handler: Callable[[], AsyncIterator[Item]]) -> AsyncIterator[Item]:
        try:
            async for item in handler():
                yield item
        except RuntimeError:
            yield Item(id=-1)

    def sync_boom(self: Any) -> Iterator[Item]:
        yield Item(id=1)
        raise RuntimeError

    async def async_boom(self: Any) -> AsyncIterator[Item]:
        yield Item(id=1)
        raise RuntimeError

    class Items(View):
        guard = request_hook(mode="wrap")(sync_guard if kind == "sync" else async_guard)
        boom = get("/")(sync_boom if kind == "sync" else async_boom)

    assert client_for(Items).get("/").text == '{"id":1}\n{"id":-1}\n'


@pytest.mark.parametrize("kind", ["coroutine", "sync generator"])
def test_a_wrap_hook_of_an_async_stream_is_an_async_generator(kind: str) -> None:
    async def coroutine(self: Any, handler: Any) -> Any:
        return handler()  # pragma: no cover

    def sync_generator(self: Any, handler: Any) -> Any:
        yield from handler()  # pragma: no cover

    class Items(View):
        guard = request_hook(mode="wrap")(coroutine if kind == "coroutine" else sync_generator)

        @get("/")
        async def items(self) -> AsyncIterator[int]:
            yield 1  # pragma: no cover

    with pytest.raises(StreamHookError):
        Items.as_router()


class NotFoundError(Exception):
    pass


class UserNotFoundError(NotFoundError):
    pass


def load_user(x_user: Annotated[str, Header()]) -> str:
    if x_user == "ghost":
        raise UserNotFoundError(x_user)

    return x_user


def test_request_hooks_run_before_dependencies_with_the_plain_request() -> None:
    seen: list[str] = []

    def logged_user(x_user: Annotated[str, Header()]) -> str:
        seen.append("dependency")
        return x_user

    class Users(View):
        user: Annotated[str, Depends(logged_user)]

        @request_hook(mode="request")
        @classmethod
        def maintenance(cls, request: Request) -> Response | None:
            seen.append(f"first:{cls.__name__}")
            return PlainTextResponse("down", status_code=503) if request.headers.get("x-down") else None

        @request_hook(mode="request")
        @classmethod
        async def second(cls, request: Request) -> None:
            seen.append("second")

        @get("/")
        def me(self) -> str:
            return self.user

    client = client_for(Users)

    assert client.get("/", headers={"x-user": "ann"}).json() == "ann"
    # the last defined runs first, as `before` hooks do
    assert seen == ["second", "first:Users", "dependency"]

    seen.clear()
    response = client.get("/", headers={"x-down": "1"})

    assert (response.status_code, response.text) == (503, "down")
    assert seen == ["second", "first:Users"]


def test_error_hooks_handle_what_dependency_resolution_raises() -> None:
    class Users(View):
        user: Annotated[str, Depends(load_user)]

        @error_hook(exc=NotFoundError)
        @classmethod
        def not_found(cls, request: Request, exc: Exception) -> Response:
            return JSONResponse({"missing": str(exc)}, status_code=404)

        @request_hook(mode="before")
        def blocked(self, x_block: Annotated[str, Header()] = "") -> None:
            if x_block:
                raise NotFoundError("blocked")

        @get("/")
        def me(self) -> str:
            if self.user == "lost":
                raise NotFoundError("route")

            return self.user

    client = client_for(Users)

    assert client.get("/", headers={"x-user": "ann"}).json() == "ann"
    assert client.get("/", headers={"x-user": "ghost"}).json() == {"missing": "ghost"}
    assert client.get("/", headers={"x-user": "ann", "x-block": "1"}).json() == {"missing": "blocked"}
    assert client.get("/", headers={"x-user": "lost"}).json() == {"missing": "route"}


def test_the_closest_error_hook_wins_and_a_subclass_wins_a_tie() -> None:
    class Base(View):
        user: Annotated[str, Depends(load_user)]

        @error_hook(exc=NotFoundError)
        @classmethod
        def any_missing(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse("base not found", status_code=404)  # pragma: no cover

        @error_hook(exc=UserNotFoundError)
        @classmethod
        def user_missing(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse("base user", status_code=404)  # pragma: no cover

        @get("/")
        def me(self) -> str:
            return self.user  # pragma: no cover

    class Child(Base):
        @error_hook(exc=UserNotFoundError)
        @classmethod
        def child_user_missing(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse("child user", status_code=404)

    assert client_for(Child).get("/", headers={"x-user": "ghost"}).text == "child user"


def test_an_unhandled_error_reaches_the_app() -> None:
    class Users(View):
        user: Annotated[str, Depends(load_user)]

        @error_hook(exc=UserNotFoundError)
        @classmethod
        def only_logs(cls, request: Request, exc: Exception) -> None:
            return None

        @get("/")
        def me(self) -> str:
            return self.user  # pragma: no cover

    app = FastAPI()
    app.include_router(Users.as_router())
    app.add_exception_handler(UserNotFoundError, lambda request, exc: PlainTextResponse("app", status_code=410))

    assert TestClient(app).get("/", headers={"x-user": "ghost"}).text == "app"


def test_an_error_hook_reshapes_validation_errors() -> None:
    class Users(View):
        @error_hook(exc=RequestValidationError)
        @classmethod
        def invalid(cls, request: Request, exc: Exception) -> Response:
            return JSONResponse({"invalid": True}, status_code=400)

        @get("/")
        def page(self, number: int) -> int:
            return number  # pragma: no cover

    response = client_for(Users).get("/", params={"number": "x"})

    assert (response.status_code, response.json()) == (400, {"invalid": True})


def test_class_hooks_keep_a_custom_route_class_and_a_prefix() -> None:
    class Timed(APIRoute):
        def get_route_handler(self) -> Any:
            handler = super().get_route_handler()

            async def timed(request: Request) -> Response:
                response = await handler(request)
                response.headers["x-timed"] = "1"
                return response

            return timed

    class Users(View, route_class=Timed):
        @request_hook(mode="request")
        @classmethod
        def tag(cls, request: Request) -> None:
            request.state.tagged = True

        @get("/")
        def me(self) -> str:
            return "me"

    app = FastAPI()
    app.include_router(Users.as_router(), prefix="/v1")
    response = TestClient(app).get("/v1/")

    assert (response.json(), response.headers["x-timed"]) == ("me", "1")


def test_exception_types_belong_to_error_hooks() -> None:
    with pytest.raises(TypeError, match="error_hook"):
        request_hook(NotFoundError)  # type: ignore[ty:no-matching-overload]

    with pytest.raises(TypeError):
        request_hook("users", 1)  # type: ignore[ty:invalid-argument-type]

    with pytest.raises(TypeError):
        error_hook("users", NotFoundError)  # type: ignore[ty:no-matching-overload]

    with pytest.raises(TypeError):
        error_hook(NotFoundError, exc=KeyError)  # type: ignore[ty:invalid-argument-type]

    with pytest.raises(TypeError):
        error_hook((), "users")

    with pytest.raises(TypeError):
        error_hook("users", exc=())

    with pytest.raises(TypeError):
        error_hook("users", exc=(NotFoundError, int))  # type: ignore[ty:invalid-argument-type]


def test_a_star_hook_applies_to_every_route() -> None:
    class Users(View):
        @request_hook("*")
        def shout(self, result: str) -> str:
            return result.upper()

        @error_hook((KeyError, ValueError), "*")
        @classmethod
        def bad(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse(type(exc).__name__, status_code=400)

        @get("/a")
        def a(self) -> str:
            return "a"

        @get("/b")
        def b(self) -> str:
            raise KeyError("b")

    client = client_for(Users)

    assert client.get("/a").json() == "A"
    assert client.get("/b").text == "KeyError"


def test_a_bare_error_hook_handles_any_exception_and_routes_narrow_it() -> None:
    class Users(View):
        @error_hook
        @classmethod
        def anything(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse(f"any:{exc}", status_code=500)

        @error_hook(ValueError, "parse")
        @classmethod
        def bad_value(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse(f"value:{exc}", status_code=400)

        @get("/parse")
        def parse(self) -> str:
            raise ValueError("parse")

        @get("/other")
        def other(self) -> str:
            raise ValueError("other")

    client = client_for(Users)

    assert client.get("/parse").text == "value:parse"
    assert client.get("/other").text == "any:other"


def test_a_hook_is_still_a_method() -> None:
    class Users(View):
        @request_hook
        def shout(self, result: str) -> str:
            return result.upper()

        @error_hook(exc=UserNotFoundError)
        @classmethod
        def missing(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse(cls.__name__)

    assert Users().shout("a") == "A"
    assert Users.missing(None, UserNotFoundError()).body == b"Users"  # type: ignore[ty:invalid-argument-type]


def test_a_route_wrapped_by_another_decorator_is_still_async() -> None:
    def traced[**P, R](func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return func(*args, **kwargs)

        return wrapper

    class Users(View):
        @request_hook
        def shout(self, result: str) -> str:
            return result.upper()

        @get("/")
        @traced
        async def users(self) -> str:
            return "a"

    assert client_for(Users).get("/").json() == "A"


# regressions


def test_an_unknown_hook_mode_fails() -> None:
    with pytest.raises(TypeError, match="unknown hook mode"):
        request_hook(mode="befor")  # type: ignore[ty:invalid-argument-type]


def test_a_hook_is_the_method_kind_it_runs_as() -> None:
    with pytest.raises(TypeError, match="classmethod"):

        class Users(View):
            @request_hook(mode="request")
            def early(self, request: Request) -> None: ...

    with pytest.raises(TypeError, match="plain method"):

        class Others(View):
            @request_hook
            @classmethod
            def late(cls, result: int) -> int:
                return result  # pragma: no cover


def test_hooks_that_cant_run_on_a_websocket_route_fail() -> None:
    class Chat(View):
        @request_hook("chat", mode="request")
        @classmethod
        def early(cls, request: Request) -> None: ...

        @websocket("/ws")
        async def chat(self, websocket: WebSocket) -> None: ...

    with pytest.raises(UnsupportedHookError):
        Chat.as_router()


def test_a_route_is_websocket_or_http() -> None:
    with pytest.raises(TypeError, match="websocket"):

        class Chat(View):
            @get("/")
            @websocket("/ws")
            async def chat(self, websocket: WebSocket) -> None: ...


# error hooks on the instance


def test_a_method_error_hook_gets_the_view_and_falls_back_to_the_class_one() -> None:
    class Users(View):
        tenant: Annotated[str, Header()] = "public"

        @error_hook(KeyError)
        def missing(self, exc: Exception) -> Response | None:
            if self.tenant == "quiet":
                return None

            return PlainTextResponse(f"{self.tenant}:{exc}", status_code=404)

        @error_hook(Exception)
        @classmethod
        def anything(cls, request: Request, exc: Exception) -> Response:
            return PlainTextResponse(f"class:{type(exc).__name__}", status_code=500)

        @get("/sync")
        def sync(self, number: int = 0) -> int:
            raise KeyError("sync")

        @get("/async")
        async def async_(self) -> int:
            raise KeyError("async")

    client = client_for(Users)

    assert client.get("/sync", headers={"tenant": "acme"}).text == "acme:'sync'"
    assert client.get("/async").text == "public:'async'"
    # it returns `None`: the classmethod hook answers
    assert client.get("/sync", headers={"tenant": "quiet"}).text == "class:KeyError"
    # raised before the view exists: only the classmethod hook sees it
    assert client.get("/sync", params={"number": "x"}).text == "class:RequestValidationError"


def test_a_method_error_hook_cant_be_named_on_a_stream() -> None:
    class Feed(View):
        @error_hook(KeyError, "feed")
        def missing(self, exc: Exception) -> Response:
            return PlainTextResponse("x")  # pragma: no cover

        @get("/")
        def feed(self) -> Iterator[int]:
            yield 1  # pragma: no cover

    with pytest.raises(UnsupportedHookError, match="stream"):
        Feed.as_router()

from collections.abc import AsyncIterator, Iterator
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from fastapi_cls_view import (
    CurrentRoute,
    Handler,
    NotAViewRouteError,
    OverrideError,
    UnknownDependencyError,
    UnknownFieldError,
    UnknownRouteError,
    View,
    current_route,
    dependency,
    error_hook,
    get,
    lifespan,
    post,
    request_hook,
    sse,
    websocket,
)


def client_for(*views: type[View]) -> TestClient:
    app = FastAPI()

    for view in views:
        app.include_router(view.as_router())

    return TestClient(app)


# current route


def test_the_current_route_is_a_field_hooks_can_use() -> None:
    seen: list[str] = []

    class Users(View):
        route: CurrentRoute

        @request_hook("*", mode="wrap")
        async def audit(self, handler: Handler[Any]) -> Any:
            seen.append(f"{self.route.method} {self.route.route.qualname}")
            return await handler()  # type: ignore[misc]

        @get("/")
        def users(self) -> str:
            return self.route.name

    assert client_for(Users).get("/").json() == "users"
    assert seen == ["GET test_the_current_route_is_a_field_hooks_can_use.<locals>.Users.users"]


def test_the_current_route_from_a_request_hook_and_a_websocket() -> None:
    seen: list[str] = []

    class Chat(View):
        @request_hook(mode="request")
        @classmethod
        def early(cls, request: Request) -> None:
            seen.append(current_route(request).name)

        @get("/")
        def page(self) -> int:
            return 1

        @websocket("/ws")
        async def chat(self, websocket: WebSocket, route: CurrentRoute) -> None:
            await websocket.accept()
            await websocket.send_text(route.name)
            await websocket.close()

    client = client_for(Chat)
    client.get("/")

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_text() == "chat"

    assert seen == ["page"]


# dependency methods


def test_a_dependency_method_runs_once_per_request_with_the_view() -> None:
    calls: list[int] = []

    class Users(View):
        factor: int = 10

        @dependency
        def user(self, user_id: int) -> int:
            calls.append(user_id)
            return user_id * self.factor

        @request_hook(mode="before")
        def check(self, user: Annotated[int, Depends(user)]) -> None:
            if user > 100:
                raise HTTPException(403)

        @get("/{user_id}")
        def read(self, user: Annotated[int, Depends(user)]) -> int:
            return user

    client = client_for(Users)

    assert client.get("/3").json() == 30
    assert calls == [3]
    assert client.get("/30").status_code == 403


def test_a_subclass_overrides_a_dependency_method() -> None:
    class Base(View):
        @dependency
        def user(self) -> str:
            return "base"

        @get("/")
        def read(self, user: Annotated[str, Depends(user)]) -> str:
            return user

    class Child(Base):
        def user(self) -> str:
            return "child"

    assert client_for(Child).get("/").json() == "child"
    assert Child().user() == "child"


def test_a_view_uses_another_views_dependency() -> None:
    class Users(View):
        @dependency
        def user(self, user_id: int) -> str:
            return f"user{user_id}"

    class Posts(View, prefix="/users/{user_id}/posts"):
        @get("/")
        def posts(self, user: Annotated[str, Depends(Users.user)]) -> list[str]:
            return [f"{user}-post"]

    assert client_for(Posts).get("/users/1/posts/").json() == ["user1-post"]


# lifespan


def test_lifespan_hooks_run_with_the_app_bases_first() -> None:
    events: list[str] = []

    class Base(View):
        @lifespan
        @classmethod
        async def pool(cls, app: FastAPI) -> AsyncIterator[None]:
            events.append(f"pool:{cls.__name__}")
            yield
            events.append("pool closed")

        @get("/")
        def page(self) -> int:
            return 1

    class Users(Base):
        @lifespan
        @classmethod
        async def cache(cls, app: FastAPI) -> AsyncIterator[None]:
            events.append("cache")
            yield
            events.append("cache closed")

    with client_for(Users) as client:
        client.get("/")
        assert events == ["pool:Users", "cache"]

    assert events == ["pool:Users", "cache", "cache closed", "pool closed"]


def test_lifespan_is_an_async_generator_classmethod() -> None:
    with pytest.raises(TypeError, match="async generator"):

        class Users(View):
            @lifespan
            @classmethod
            def pool(cls, app: FastAPI) -> None: ...


# background


def test_background_hooks_run_after_the_response_in_order() -> None:
    events: list[str] = []

    class Users(View):
        @request_hook(mode="background")
        def audit(self) -> None:
            events.append("audit")

        @request_hook(mode="background")
        async def notify(self) -> None:
            events.append("notify")

        @get("/")
        def ok(self) -> int:
            events.append("route")
            return 1

        @error_hook(KeyError)
        def missing(self, exc: Exception) -> Response:
            return JSONResponse({"missing": True})

        @get("/fail")
        def fail(self) -> int:
            raise HTTPException(400)

        @get("/handled")
        def handled(self) -> int:
            raise KeyError

        @websocket("/ws")
        async def chat(self, websocket: WebSocket) -> None:
            await websocket.accept()
            await websocket.close()

    client = client_for(Users)

    assert client.get("/").json() == 1
    assert events == ["route", "audit", "notify"]

    events.clear()

    assert client.get("/fail").status_code == 400
    assert client.get("/handled").json() == {"missing": True}

    with client.websocket_connect("/ws"):
        pass

    assert events == []


# overrides


def test_override_replaces_fields_for_the_duration() -> None:
    def real_repo() -> str:
        return "real"

    class Users(View):
        repo: Annotated[str, Depends(real_repo)]
        page: int = 1

        @get("/")
        def users(self) -> list[Any]:
            return [self.repo, self.page]

    app = FastAPI()
    app.include_router(Users.as_router())
    client = TestClient(app)

    with Users.override(app, repo="fake"):
        assert client.get("/", params={"page": 2}).json() == ["fake", 2]

    assert client.get("/").json() == ["real", 1]

    with pytest.raises(UnknownFieldError), Users.override(app, nope=1):
        pass  # pragma: no cover


# route selection


def test_as_router_picks_routes() -> None:
    class Users(View):
        @get("/")
        def list_users(self) -> int:
            return 1  # pragma: no cover

        @post("/")
        def create(self) -> int:
            return 1  # pragma: no cover

        @get("/{user_id}")
        def read(self, user_id: int) -> int:
            return user_id  # pragma: no cover

    def names(router: Any) -> set[str]:
        return {route.name.rsplit(".", 1)[-1] for route in router.routes}

    assert names(Users.as_router(include={"list_users", "read"})) == {"list_users", "read"}
    assert names(Users.as_router(exclude={"create"})) == {"list_users", "read"}

    with pytest.raises(UnknownRouteError):
        Users.as_router(exclude={"delete"})


# teardown


def test_teardown_hooks_see_the_outcome_last_defined_first() -> None:
    events: list[Any] = []

    class Users(View):
        @request_hook(mode="teardown")
        def commit(self, exc: Exception | None) -> None:
            events.append(("commit", type(exc).__name__ if exc else None))

        @request_hook(mode="teardown")
        async def close(self, exc: Exception | None) -> None:
            events.append(("close", type(exc).__name__ if exc else None))

        @error_hook(KeyError)
        @classmethod
        def missing(cls, request: Request, exc: Exception) -> Response:
            return JSONResponse({"missing": True})

        @get("/")
        def ok(self) -> int:
            return 1

        @get("/fail")
        def fail(self) -> int:
            raise KeyError("x")

    client = client_for(Users)

    assert client.get("/").json() == 1
    assert events == [("close", None), ("commit", None)]

    events.clear()

    # the error hook answers, the teardown still sees the failure
    assert client.get("/fail").json() == {"missing": True}
    assert events == [("close", "KeyError"), ("commit", "KeyError")]


def test_a_stream_is_torn_down_after_it_is_sent() -> None:
    events: list[str] = []

    class Feed(View):
        @request_hook(mode="teardown")
        def done(self, exc: Exception | None) -> None:
            events.append("done")

        @get("/")
        def feed(self) -> Iterator[int]:
            for item in range(2):
                events.append(f"item{item}")
                yield item

    assert client_for(Feed).get("/").text == "0\n1\n"
    assert events == ["item0", "item1", "done"]


def test_dependency_errors() -> None:
    with pytest.raises(TypeError, match="dependency"):
        dependency(1)  # type: ignore[ty:invalid-argument-type]

    class Users(View):
        @dependency
        def user(self) -> str:
            return "u"  # pragma: no cover

    with pytest.raises(OverrideError):

        class Child(Users):
            user = 1

    class Dropped(Users):
        user = None

    unbound = dependency(Users.user.func)  # not on `Other`

    class Other(View):
        @get("/")
        def read(self, user: Annotated[str, Depends(unbound)]) -> str:
            return user  # pragma: no cover

    assert "user" not in Dropped.__view_dependencies__

    with pytest.raises(UnknownDependencyError):
        _ = Other.read.endpoint


def test_the_current_route_outside_a_view() -> None:
    app = FastAPI()

    @app.get("/")
    def plain(route: CurrentRoute) -> str:
        return route.name  # pragma: no cover

    with pytest.raises(NotAViewRouteError):
        TestClient(app).get("/")


def test_the_route_context_knows_its_view() -> None:
    class Users(View):
        route: CurrentRoute

        @get("/")
        def users(self) -> str:
            return self.route.view.__name__

    assert client_for(Users).get("/").json() == "Users"


# url_for


def test_url_for_is_absolute_and_follows_the_root_path() -> None:
    class Users(View):
        request: Request

        @get("/{user_id}")
        def read(self, user_id: int) -> str:
            return str(Users.read.url_for(self.request, user_id=user_id))

        @post("/", status_code=201)
        def create(self, response: Response) -> int:
            response.headers["location"] = str(Users.read.url_for(self.request, user_id=7))
            return 7

    app = FastAPI()
    app.include_router(Users.as_router())
    client = TestClient(app, root_path="/api")

    assert client.get("/1").json() == "http://testserver/api/1"
    assert client.post("/").headers["location"] == "http://testserver/api/7"


# server-sent events


def test_sse_streams_events_through_after_hooks() -> None:
    class Feed(View):
        @request_hook("events")
        def shout(self, item: str) -> str:
            return item.upper()

        @sse("/events")
        async def events(self) -> AsyncIterator[str]:
            for item in ("a", "b"):
                yield item

    response = client_for(Feed).get("/events")

    assert response.headers["content-type"].startswith("text/event-stream")
    assert [line for line in response.text.splitlines() if line] == ['data: "A"', 'data: "B"']

    with pytest.raises(TypeError, match="EventSourceResponse"):
        sse("/", response_class=Response)

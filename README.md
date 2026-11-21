# fastapi-cls-view

Class-based views for FastAPI.

A view is a dataclass: its fields are dependencies, its methods are routes, and hooks run around them the way validators run around a pydantic model. Everything goes through FastAPI's own machinery - `Depends`, `APIRouter`, OpenAPI - so nothing you know stops working.

## Installation

```sh
pip install fastapi-cls-view
```

Requires Python 3.12+ and FastAPI 0.165+.

## Usage

```python
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status

from fastapi_cls_view import View, get, post


class Users(View, prefix="/users", tags=["users"]):
    repo: Annotated[UserRepo, Depends(get_repo)]
    limit: int = 10

    @get("/")
    def index(self) -> list[User]:
        return self.repo.all()[: self.limit]

    @get("/{user_id}")
    def read(self, user_id: int) -> User:
        if (user := self.repo.get(user_id)) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)

        return user

    @post("/", status_code=status.HTTP_201_CREATED)
    def create(self, user: UserIn) -> User:
        return self.repo.add(user)


app = FastAPI()
app.include_router(Users.as_router())
```

- The view is built once per request. Each field is a parameter of it, resolved as FastAPI resolves a route's: `Depends`, a query parameter with a default, `Request`, another view.
- `get`, `post`, `put`, `patch`, `delete`, `options`, `head`, `trace`, `api_route`, `websocket` and `sse` take what the `APIRouter` methods take.
- The class keywords are the `APIRouter` options: `prefix`, `tags`, `dependencies`, `responses` and the rest. `view_config = ViewConfig(...)` in the class body does the same.
- `as_router(include=..., exclude=..., **overrides)` picks routes by name and overrides the config.
- `Users.read` is the route - `Users.read.url_for(request, user_id=1)` - while `Users(...).read(1)` stays a plain method call.

## Hooks

```python
from fastapi_cls_view import Handler, View, error_hook, get, request_hook, responds


class Items(View, prefix="/items"):
    @request_hook(mode="before")
    def authenticate(self, x_token: Annotated[str | None, Header()] = None) -> None:
        if x_token != "secret":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    @request_hook("read")  # only for `read`; no names, or "*", is every route
    def shout(self, result: Item) -> Item:
        return result.model_copy(update={"name": result.name.upper()})

    @request_hook(mode="wrap")
    def traced(self, handler: Handler[Any]) -> Any:
        with span("items"):
            return handler()

    @responds(status.HTTP_404_NOT_FOUND, description="No such item")
    @error_hook(ItemNotFoundError)
    def not_found(self, exc: Exception) -> Response:
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_404_NOT_FOUND)
```

| Mode | Signature | Runs |
|---|---|---|
| `request` | `(cls, request) -> Response \| None` | Before the view is built, with the plain request. A response answers the request. |
| `before` | `(self, ...dependencies) -> None` | As a dependency of the route, with parameters of its own. |
| `after` (default) | `(self, result) -> result` | With what the route returned - or each item of a stream. |
| `wrap` | `(self, handler) -> result` | Around the route and the hooks defined before it. |
| `teardown` | `(self, exc: Exception \| None) -> None` | Once the route is done, with its error if it failed. |
| `background` | `(self) -> None` | Once the response is sent. Not at all if the request failed, even when an error hook answered it. |

An `@error_hook(ExcType)` answers an exception with a response; the closest exception type wins. As a method it handles what the route raises, as a `@classmethod` - `(cls, request, exc)` - also what building the view raises. A bare `@error_hook` leaves FastAPI's own `HTTPException` and validation errors alone.

`@responds(...)` documents a response of a route, or of every route a hook applies to.

## Dependencies

A method can be a dependency: resolved once per request, with the view.

```python
class Users(View, prefix="/users"):
    @dependency
    def user(self, user_id: int) -> User:
        return find_user(user_id)

    @get("/{user_id}")
    def read(self, user: Annotated[User, Depends(user)]) -> User:
        return user


class Posts(View, prefix="/users/{user_id}/posts"):
    route: CurrentRoute  # the route being served

    @get("/")
    def index(self, user: Annotated[User, Depends(Users.user)]) -> list[Post]:
        return posts_of(user)
```

Outside a view, depend on `Users.user.dependency`.

## Lifespan

A `@lifespan` hook runs with the app: startup before the `yield`, shutdown after it. The bases' hooks start first and shut down last.

```python
class Users(View, prefix="/users"):
    pool: ClassVar[Pool]

    @lifespan
    @classmethod
    async def open_pool(cls, app: FastAPI) -> AsyncIterator[None]:
        async with create_pool() as cls.pool:
            yield
```

## Inheritance and generics

Routes, hooks, dependencies and config are inherited as Python resolves attributes. Override a route with a plain method, drop it with `None`.

```python
class CRUD[M: BaseModel](View):
    @get("/{item_id}")
    def read(self, item_id: int) -> M: ...

    @put("/{item_id}")
    def save(self, item_id: int, item: M) -> M: ...


class Tags(CRUD[Tag], prefix="/tags"):
    save = None  # read-only


app.include_router(Tags.as_router())
app.include_router(CRUD[User].as_router(prefix="/users"))
```

## Testing

```python
with Users.override(app, repo=FakeRepo()):
    assert client.get("/users/").json() == [...]
```

## Examples

Working apps for each of the above are in [`examples/`](examples), on SQLAlchemy with an in-memory SQLite database:

| Example | Shows | With |
|---|---|---|
| [`basic`](examples/basic.py) | A CRUD view with a session field | SQLAlchemy |
| [`hooks`](examples/hooks.py) | A transaction per request, an API key, timing, an audit log, 404 and 409 answers | SQLAlchemy |
| [`dependencies`](examples/dependencies.py) | A bearer-token `current_user` shared by two views | PyJWT |
| [`lifespan`](examples/lifespan.py) | A view that owns its engine | SQLAlchemy |
| [`generics`](examples/generics.py) | A generic paginated `CRUD[Out, In]` view | fastapi-pagination |
| [`streaming`](examples/streaming.py) | Rows as JSON lines and server-sent events, a websocket chat room | SQLAlchemy |
| [`overrides`](examples/overrides.py) | A test that serves a view with its own database | SQLAlchemy |

```sh
pip install "fastapi-cls-view[examples]"
fastapi dev examples/basic.py
```

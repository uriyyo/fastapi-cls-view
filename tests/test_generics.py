from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injected import Dep
from pydantic import BaseModel

from fastapi_cls_view import View, get, post, request_hook
from fastapi_cls_view._internal.view.generics import typevar_maps

from ._future_generic_view import Box


class User(BaseModel):
    id: int
    name: str


class Store[M]:
    def __init__(self) -> None:
        self.items: list[Any] = []


class UserStore(Store[User]):
    pass


class CRUD[Model: BaseModel, S: Store[Any]](View):
    store: Dep[S]

    @get("/")
    def items(self) -> list[Model]:
        return self.store.items

    @post("/")
    def create(self, body: Model) -> Model:
        self.store.items.append(body)
        return body

    @get("/stream")
    def stream(self) -> Iterator[Model]:
        yield from self.store.items


class Users(CRUD[User, UserStore], prefix="/users"):
    pass


def client_for(*views: type[View]) -> TestClient:
    app = FastAPI()

    for view in views:
        app.include_router(view.as_router())

    return TestClient(app)


class Paged[Item](BaseModel):
    items: list[Item]
    total: int


def test_generic_pydantic_models_are_parametrized() -> None:
    class Listing[Model: BaseModel](View):
        @get("/")
        def page(self) -> Paged[Model]:
            return Paged(items=[User(id=1, name="ann")], total=1)  # type: ignore[return-value]

    class UserListing(Listing[User]):
        pass

    app = FastAPI()
    app.include_router(UserListing.as_router())

    assert TestClient(app).get("/").json() == {"items": [{"id": 1, "name": "ann"}], "total": 1}
    schema = app.openapi()["paths"]["/"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/Paged_User_"}


def test_type_variables_through_several_bases() -> None:
    class Named[S: Store[Any]](CRUD[User, S]):
        pass

    class Concrete(Named[UserStore]):
        pass

    mapping = typevar_maps(Concrete)[CRUD]

    assert {param.__name__: arg for param, arg in mapping.items()} == {"Model": User, "S": UserStore}


def test_fields_bodies_and_responses_are_substituted() -> None:
    app = FastAPI()
    app.include_router(Users.as_router())
    client = TestClient(app)

    assert client.post("/users/", json={"id": 1, "name": "ann", "extra": True}).json() == {"id": 1, "name": "ann"}
    assert client.post("/users/", json={"id": "x"}).status_code == 422

    paths = app.openapi()["paths"]
    user = {"$ref": "#/components/schemas/User"}

    assert paths["/users/"]["post"]["requestBody"]["content"]["application/json"]["schema"] == user
    assert paths["/users/"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] == user
    assert paths["/users/stream"]["get"]["responses"]["200"]["content"]["application/jsonl"]["itemSchema"] == user


def test_a_subclass_of_a_concrete_view_keeps_its_types() -> None:
    class Admins(Users, prefix="/admins"):
        pass

    client = client_for(Users, Admins)
    client.post("/users/", json={"id": 1, "name": "ann"})

    assert client.post("/admins/", json={"id": 2, "name": "bob"}).json() == {"id": 2, "name": "bob"}
    assert client.post("/admins/", json={"id": "x"}).status_code == 422


def test_hooks_are_substituted_too() -> None:
    class Shout[Model: BaseModel](View):
        @request_hook("one")
        def upper(self, result: Model) -> Model:
            return result.model_copy(update={"name": result.name.upper()})  # ty: ignore[missing-attribute]

        @get("/")
        def one(self) -> Model:
            return User(id=1, name="ann")  # type: ignore[ty:invalid-explicit-return]

    class ShoutUsers(Shout[User]):
        pass

    assert client_for(ShoutUsers).get("/").json() == {"id": 1, "name": "ANN"}


def test_string_annotations_see_the_type_parameters() -> None:
    class Ints(Box[int]):
        pass

    client = client_for(Ints)

    assert client.get("/", params={"value": 3}).json() == [3]
    assert client.get("/", params={"value": "x"}).status_code == 422


class PublicUser(BaseModel):
    id: int


def test_an_override_can_narrow_the_return_type() -> None:
    class Base[Model: BaseModel](View):
        @get("/")
        def items(self) -> list[Model]:
            return [User(id=1, name="ann")]  # type: ignore[ty:invalid-explicit-return]

    class Users(Base[User]):
        pass

    class PublicUsers(Users):
        def items(self) -> list[PublicUser]:  # type: ignore[ty:incompatible-attribute-override]
            return super().items()  # type: ignore[ty:invalid-explicit-return]

    app = FastAPI()
    app.include_router(PublicUsers.as_router())
    schema = app.openapi()["paths"]["/"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert TestClient(app).get("/").json() == [{"id": 1}]
    assert schema["items"] == {"$ref": "#/components/schemas/PublicUser"}

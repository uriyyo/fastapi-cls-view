from typing import Any

import pytest
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from fastapi_cls_view import (
    Example,
    ResponseDoc,
    ResponseHeader,
    View,
    error_hook,
    get,
    request_hook,
    responds,
    websocket,
)


class ErrorOut(BaseModel):
    detail: str
    error_code: str = Field("missing", serialization_alias="errorCode")


class NotFoundError(Exception):
    pass


not_found = responds(404, ErrorOut, "Not found")


def responses_of(view: type[View], path: str = "/", method: str = "get") -> dict[str, Any]:
    app = FastAPI()
    app.include_router(view.as_router())
    return app.openapi()["paths"][path][method]["responses"]


def test_to_fastapi() -> None:
    doc = ResponseDoc(
        429,
        ErrorOut,
        headers={"retry-after": ResponseHeader("Seconds to wait", int), "x-trace": "Trace id"},
        examples={"slow down": Example({"detail": "slow down"}, summary="Too fast"), "plain": {"detail": "x"}},
    )

    assert doc.to_fastapi() == {
        429: {
            "description": "Too Many Requests",
            "model": ErrorOut,
            "headers": {
                "retry-after": {"description": "Seconds to wait", "schema": {"type": "integer"}},
                "x-trace": {"description": "Trace id", "schema": {"type": "string"}},
            },
            "content": {
                "application/json": {
                    "examples": {
                        "slow down": {"summary": "Too fast", "value": {"detail": "slow down"}},
                        "plain": {"value": {"detail": "x"}},
                    },
                },
            },
        },
    }
    assert ResponseDoc("4XX").to_fastapi() == {"4XX": {"description": "Response"}}
    assert ResponseDoc(200, media_type="text/csv").to_fastapi() == {
        200: {"description": "OK", "content": {"text/csv": {}}},
    }


def test_examples_can_be_model_instances() -> None:
    doc = ResponseDoc(
        404,
        ErrorOut,
        examples={"ghost": ErrorOut(detail="ghost"), "summed": Example(ErrorOut(detail="x"), summary="With a summary")},
    )

    examples = doc.to_fastapi()[404]["content"]["application/json"]["examples"]

    # serialized as FastAPI serializes a response - aliases included
    assert examples == {
        "ghost": {"value": {"detail": "ghost", "errorCode": "missing"}},
        "summed": {"summary": "With a summary", "value": {"detail": "x", "errorCode": "missing"}},
    }


def test_a_route_hook_and_view_are_documented() -> None:
    class Users(View, responses=[ResponseDoc(401, description="Not signed in")]):
        @not_found
        @error_hook("user", exc=NotFoundError)
        @classmethod
        def missing(cls, request: Request, exc: Exception) -> Response:
            return JSONResponse({"detail": str(exc)}, status_code=404)  # pragma: no cover

        @responds(409, ErrorOut, "Taken")
        @get("/{user_id}")
        def user(self, user_id: int) -> int:
            return user_id  # pragma: no cover

        @get("/")
        def users(self) -> list[int]:
            return []  # pragma: no cover

    user = responses_of(Users, "/{user_id}")

    assert {*user} == {"200", "401", "404", "409", "422"}
    assert user["404"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorOut"}
    assert user["409"]["description"] == "Taken"
    # the hook only applies to `user`
    assert {*responses_of(Users)} == {"200", "401"}


def test_the_most_specific_docs_win() -> None:
    class Users(View, responses=[ResponseDoc(404, description="from the view")]):
        @responds(404, description="from the hook", headers={"x-hook": "set by the hook"})
        @request_hook
        def after(self, result: Any) -> Any:
            return result  # pragma: no cover

        @responds(404, description="from the route")
        @get("/", responses={404: {"description": "from the route decorator"}})
        def users(self) -> list[int]:
            return []  # pragma: no cover

        @get("/plain")
        def plain(self) -> list[int]:
            return []  # pragma: no cover

    assert responses_of(Users)["404"]["description"] == "from the route decorator"
    # key by key: what a later source does not say is kept
    assert "x-hook" in responses_of(Users)["404"]["headers"]
    assert responses_of(Users, "/plain")["404"]["description"] == "from the hook"


def test_docs_are_inherited_and_kept_by_an_override() -> None:
    class Base(View):
        @not_found
        @get("/")
        def items(self) -> list[int]:
            return []  # pragma: no cover

    class Child(Base):
        def items(self) -> list[int]:
            return [1]  # pragma: no cover

    assert responses_of(Child)["404"]["description"] == "Not found"


def test_websocket_routes_are_not_documented() -> None:
    class Live(View, responses=[ResponseDoc(403)]):
        @websocket("/ws")
        async def ws(self, ws: WebSocket) -> None:
            pass  # pragma: no cover

    app = FastAPI()
    app.include_router(Live.as_router())

    assert app.openapi()["paths"] == {}


def test_responds_documents_routes_and_hooks_only() -> None:
    def plain(self: Any) -> None:
        pass  # pragma: no cover

    with pytest.raises(TypeError, match="put it above"):
        responds(404)(plain)

    with pytest.raises(TypeError):
        responds(404)(View)

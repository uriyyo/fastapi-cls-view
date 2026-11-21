from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from examples import basic, db, dependencies, generics, hooks, lifespan, overrides, streaming


def test_basic() -> None:
    with TestClient(basic.app) as client:
        assert client.post("/users/", json={"name": "Ada"}).json() == {"id": 1, "name": "Ada"}
        assert client.post("/users/", json={"name": "Grace"}).status_code == 201
        assert client.get("/users/1").json() == {"id": 1, "name": "Ada"}
        assert len(client.get("/users/", params={"limit": 1}).json()) == 1
        assert client.delete("/users/2").status_code == 204
        assert client.get("/users/2").status_code == 404


def test_hooks() -> None:
    with TestClient(hooks.app, headers={"x-api-key": hooks.API_KEY}) as client:
        tea = {"user_id": 1, "title": "tea"}

        assert client.post("/posts/", json=tea).json() == {"id": 1, **tea}
        assert client.get("/posts/1").json() == {"id": 1, **tea}
        assert client.post("/posts/", json=tea).status_code == 409
        assert client.get("/posts/2").json() == {"detail": "post not found"}
        assert len(client.get("/posts/").json()) == 1
        assert client.get("/posts/", headers={"x-api-key": "nope"}).status_code == 401

        with Session(db.engine) as session:
            assert session.scalars(select(db.AuditLog.action)).all() == ["Posts.create"]

    assert {"404", "409"} <= {*hooks.app.openapi()["paths"]["/posts/{post_id}"]["get"]["responses"]}


def test_dependencies() -> None:
    with TestClient(dependencies.app) as client:
        token = client.post("/auth/register", json={"name": "Ada"}).json()["access_token"]
        auth = {"authorization": f"Bearer {token}"}

        assert client.get("/auth/me", headers=auth).json() == {"id": 1, "name": "Ada"}
        assert client.post("/me/posts/", params={"title": "Notes"}, headers=auth).status_code == 201
        assert client.get("/me/posts/", headers=auth).json() == [{"id": 1, "user_id": 1, "title": "Notes"}]
        assert client.get("/me/posts/", headers={"authorization": "Bearer nope"}).status_code == 401


def test_lifespan() -> None:
    with TestClient(lifespan.app) as client:
        assert client.get("/health/").json()["users"] == 0


def test_generics() -> None:
    with TestClient(generics.app) as client:
        assert client.post("/users/", json={"name": "Ada"}).json() == {"id": 1, "name": "Ada"}
        assert client.post("/posts/", json={"user_id": 1, "title": "Notes"}).status_code == 201

        page = client.get("/users/", params={"size": 1}).json()

        assert page["items"] == [{"id": 1, "name": "Ada"}]
        assert page["total"] == 1
        assert client.get("/users/2").json() == {"detail": "user not found"}
        assert client.delete("/users/1").status_code == 204
        assert client.delete("/posts/1").status_code == 405

    schema = generics.app.openapi()["paths"]["/posts/"]["get"]["responses"]["200"]["content"]["application/json"]

    assert schema["schema"]["$ref"].endswith("Page_PostOut_")


def test_streaming() -> None:
    with TestClient(streaming.app) as client:
        for name in ("Ada", "Grace"):
            client.post("/users/", json={"name": name})

        assert client.get("/export/users").text == '{"name":"Ada","id":1}\n{"name":"Grace","id":2}\n'
        assert client.get("/export/users/events").text.startswith('data: {"name":"Ada","id":1}')

        with client.websocket_connect("/chat/ws") as ws:
            ws.send_text("hi")
            assert ws.receive_text() == "hi"


def test_overrides() -> None:
    overrides.test_users_with_their_own_database()

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from fastapi_cls_view.decorators import get
from fastapi_cls_view.views import View


@dataclass
class UsersService:
    def get_users(self):
        return [{"id": 1, "name": "John Doe"}]


class UsersView(View):
    service: Annotated[UsersService, Depends()]

    @get("/")
    def users(self):
        return {"users": self.service.get_users()}


app = FastAPI()

app.include_router(UsersView.as_router())


def test_register() -> None:
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.json() == {"users": [{"id": 1, "name": "John Doe"}]}

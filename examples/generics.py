from typing import Any, ClassVar

from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse
from fastapi_pagination import Page, add_pagination
from fastapi_pagination.ext.sqlalchemy import paginate
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from examples.db import Post, PostIn, PostOut, SessionDep, User, UserIn, UserOut, lifespan
from fastapi_cls_view import View, delete, error_hook, get, post, responds


class CRUD[Out: BaseModel, In: BaseModel](View):
    model: ClassVar[type[Any]]

    session: SessionDep

    @responds(status.HTTP_404_NOT_FOUND, description="Not found")
    @error_hook(NoResultFound)
    def not_found(self, exc: Exception) -> Response:
        return JSONResponse(
            {"detail": f"{self.model.__name__.lower()} not found"},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @get("/")
    def index(self) -> Page[Out]:
        return paginate(self.session, select(self.model).order_by(self.model.id))

    @get("/{item_id}")
    def read(self, item_id: int) -> Out:
        return self.session.get_one(self.model, item_id)

    @post("/", status_code=status.HTTP_201_CREATED)
    def create(self, data: In) -> Out:
        item = self.model(**data.model_dump())
        self.session.add(item)
        self.session.commit()

        return item

    @delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove(self, item_id: int) -> None:
        self.session.delete(self.session.get_one(self.model, item_id))
        self.session.commit()


class Users(CRUD[UserOut, UserIn], prefix="/users", tags=["users"]):
    model = User


class Posts(CRUD[PostOut, PostIn], prefix="/posts", tags=["posts"]):
    model = Post

    # posts are kept: drops the inherited route
    remove = None


app = FastAPI(lifespan=lifespan)
app.include_router(Users.as_router())
app.include_router(Posts.as_router())
add_pagination(app)

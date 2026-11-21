import logging
import time
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.orm import Session

from examples.db import AuditLog, Post, PostIn, PostOut, SessionDep, engine, lifespan
from fastapi_cls_view import CurrentRoute, Handler, View, error_hook, get, post, request_hook, responds

logger = logging.getLogger(__name__)

API_KEY = "example-key"


class Posts(View, prefix="/posts", tags=["posts"]):
    session: SessionDep
    route: CurrentRoute

    # before the view is built, with the plain request: a response here answers the request
    @request_hook(mode="request")
    @classmethod
    def api_key(cls, request: Request) -> Response | None:
        if request.headers.get("x-api-key") != API_KEY:
            return JSONResponse({"detail": "invalid API key"}, status_code=status.HTTP_401_UNAUTHORIZED)

        return None

    # around the route
    @request_hook(mode="wrap")
    def timed(self, handler: Handler[Any]) -> Any:
        start = time.perf_counter()

        try:
            return handler()
        finally:
            logger.info("%s took %.1f ms", self.route.name, (time.perf_counter() - start) * 1000)

    # the default mode, `after`: gets what the route returned - here only for `create`
    @request_hook("create")
    def flushed(self, result: Post) -> Post:
        self.session.flush()  # the id is set, a duplicate title fails here
        return result

    # one transaction per request: once the route is done, with its error if it failed
    @request_hook(mode="teardown")
    def finish(self, exc: Exception | None) -> None:
        if exc is None:
            self.session.commit()
        else:
            self.session.rollback()

    # once the response is sent
    @request_hook("create", mode="background")
    def audit(self) -> None:
        with Session(engine) as session:
            session.add(AuditLog(action=self.route.route.qualname))
            session.commit()

    @responds(status.HTTP_404_NOT_FOUND, description="No such post")
    @error_hook(NoResultFound)
    def not_found(self, exc: Exception) -> Response:
        return JSONResponse({"detail": "post not found"}, status_code=status.HTTP_404_NOT_FOUND)

    @responds(status.HTTP_409_CONFLICT, description="The title is taken")
    @error_hook(IntegrityError)
    def conflict(self, exc: Exception) -> Response:
        return JSONResponse({"detail": "title already exists"}, status_code=status.HTTP_409_CONFLICT)

    @get("/", response_model=list[PostOut])
    def index(self) -> Sequence[Post]:
        return self.session.scalars(select(Post).order_by(Post.id)).all()

    @get("/{post_id}", response_model=PostOut)
    def read(self, post_id: int) -> Post:
        return self.session.get_one(Post, post_id)

    @post("/", status_code=status.HTTP_201_CREATED, response_model=PostOut)
    def create(self, data: PostIn) -> Post:
        new = Post(user_id=data.user_id, title=data.title)
        self.session.add(new)

        return new


app = FastAPI(lifespan=lifespan)
app.include_router(Posts.as_router())

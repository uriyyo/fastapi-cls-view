from collections.abc import Sequence
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from examples.db import Post, PostOut, SessionDep, User, UserIn, UserOut, lifespan
from fastapi_cls_view import View, dependency, get, post

SECRET = "an-example-secret-of-at-least-32-bytes"  # noqa: S105
bearer = HTTPBearer()


class Auth(View, prefix="/auth", tags=["auth"]):
    session: SessionDep

    # a method as a dependency: resolved once per request, with the view
    @dependency
    def current_user(self, credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> User:
        try:
            payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from None

        if (user := self.session.get(User, int(payload["sub"]))) is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")

        return user

    @post("/register")
    def register(self, data: UserIn) -> dict[str, str]:
        user = User(name=data.name)
        self.session.add(user)
        self.session.commit()

        return {"access_token": jwt.encode({"sub": str(user.id)}, SECRET, algorithm="HS256")}

    @get("/me", response_model=UserOut)
    def me(self, user: Annotated[User, Depends(current_user)]) -> User:
        return user


class MyPosts(View, prefix="/me/posts", tags=["posts"]):
    session: SessionDep

    # another view's dependency
    @get("/", response_model=list[PostOut])
    def index(self, user: Annotated[User, Depends(Auth.current_user)]) -> Sequence[Post]:
        return self.session.scalars(select(Post).where(Post.user_id == user.id)).all()

    @post("/", status_code=status.HTTP_201_CREATED, response_model=PostOut)
    def create(self, title: str, user: Annotated[User, Depends(Auth.current_user)]) -> Post:
        new = Post(user_id=user.id, title=title)
        self.session.add(new)
        self.session.commit()

        return new


app = FastAPI(lifespan=lifespan)
app.include_router(Auth.as_router())
app.include_router(MyPosts.as_router())

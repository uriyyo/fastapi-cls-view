from collections.abc import Sequence

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import select

from examples.db import SessionDep, User, UserIn, UserOut, lifespan
from fastapi_cls_view import View, delete, get, post


class Users(View, prefix="/users", tags=["users"]):
    # fields are dependencies, resolved once per request
    session: SessionDep
    limit: int = 10

    @get("/", response_model=list[UserOut])
    def index(self) -> Sequence[User]:
        return self.session.scalars(select(User).order_by(User.id).limit(self.limit)).all()

    @get("/{user_id}", response_model=UserOut)
    def read(self, user_id: int) -> User:
        if (user := self.session.get(User, user_id)) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)

        return user

    @post("/", status_code=status.HTTP_201_CREATED, response_model=UserOut)
    def create(self, data: UserIn) -> User:
        user = User(name=data.name)
        self.session.add(user)
        self.session.commit()

        return user

    @delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove(self, user_id: int) -> None:
        self.session.delete(self.read(user_id))
        self.session.commit()


app = FastAPI(lifespan=lifespan)
app.include_router(Users.as_router())

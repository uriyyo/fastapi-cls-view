from collections.abc import AsyncIterator
from typing import ClassVar

from fastapi import FastAPI
from sqlalchemy import Engine, func, select

from examples.db import Base, User, make_engine
from fastapi_cls_view import View, get, lifespan


class Health(View, prefix="/health"):
    engine: ClassVar[Engine]

    # runs with the app: startup before the `yield`, shutdown after it
    @lifespan
    @classmethod
    async def database(cls, app: FastAPI) -> AsyncIterator[None]:
        cls.engine = make_engine()
        Base.metadata.create_all(cls.engine)
        yield
        cls.engine.dispose()

    @get("/")
    def check(self) -> dict[str, int | str | None]:
        with self.engine.connect() as connection:
            return {
                "sqlite": connection.scalar(select(func.sqlite_version())),
                "users": connection.scalar(select(func.count()).select_from(User)),
            }


app = FastAPI()
app.include_router(Health.as_router())

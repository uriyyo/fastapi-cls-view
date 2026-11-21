from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, ForeignKey, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


def make_engine() -> Engine:
    # one in-memory database, shared by the threads FastAPI runs sync code in
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


engine = make_engine()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(unique=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str]


class UserIn(BaseModel):
    name: str


class UserOut(UserIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class PostIn(BaseModel):
    user_id: int
    title: str


class PostOut(PostIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


type SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)

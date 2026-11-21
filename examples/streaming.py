from collections.abc import Iterator
from typing import ClassVar

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from examples.basic import Users
from examples.db import SessionDep, User, UserOut, lifespan
from fastapi_cls_view import View, get, request_hook, sse, websocket


class Export(View, prefix="/export", tags=["export"]):
    session: SessionDep

    # on a stream, an `after` hook gets each item
    @request_hook
    def to_schema(self, user: User) -> UserOut:
        return UserOut.model_validate(user)

    def _users(self) -> Iterator[User]:
        yield from self.session.scalars(select(User).order_by(User.id)).yield_per(100)

    # JSON lines, a row at a time
    @get("/users")
    def users(self) -> Iterator[UserOut]:
        yield from self._users()  # type: ignore[ty:invalid-yield]

    # the same rows as server-sent events
    @sse("/users/events")
    def events(self) -> Iterator[UserOut]:
        yield from self._users()  # type: ignore[ty:invalid-yield]


class Chat(View, prefix="/chat"):
    connections: ClassVar[set[WebSocket]] = set()

    @websocket("/ws")
    async def room(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)

        try:
            while True:
                message = await websocket.receive_text()

                for connection in self.connections:
                    await connection.send_text(message)
        except WebSocketDisconnect:
            self.connections.discard(websocket)


app = FastAPI(lifespan=lifespan)
app.include_router(Users.as_router())  # to add the users to export
app.include_router(Export.as_router())
app.include_router(Chat.as_router())

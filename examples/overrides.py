from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from examples.basic import Users, app
from examples.db import Base, User, make_engine


def test_users_with_their_own_database() -> None:
    engine = make_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(User(name="Fake"))
        session.commit()

        # the view is served with these field values, for the duration
        with Users.override(app, session=session):
            assert TestClient(app).get("/users/").json() == [{"id": 1, "name": "Fake"}]

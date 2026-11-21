from __future__ import annotations

from typing import Annotated

from fastapi import Query

from fastapi_cls_view import View, get


class Box[T](View):
    value: Annotated[T, Query()]

    @get("/")
    def read(self) -> list[T]:
        return [self.value]

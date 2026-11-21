from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from types import MethodType
from typing import TYPE_CHECKING, Any, ClassVar, Concatenate, Self, overload

from fastapi_cls_view.errors import UnboundRouteError

if TYPE_CHECKING:
    from fastapi_cls_view.views import View


class ViewMember[S, **P, R]:
    """A method of a view the view collects - a route, a dependency: a plain method on an instance."""

    __slots__ = ()

    # the view's mapping of these, by name
    collected_in: ClassVar[str]

    if TYPE_CHECKING:
        # the dataclass fields of each member

        @property
        def name(self) -> str: ...

        @property
        def func(self) -> Callable[Concatenate[S, P], R]: ...

        @property
        def owner(self) -> type[View] | None: ...

    @property
    def view(self) -> type[View]:
        if self.owner is None:
            raise UnboundRouteError(self.name)

        return self.owner

    @property
    def qualname(self) -> str:
        match self.owner:
            case None:
                return f"<unbound>.{self.name}"
            case owner:
                return f"{owner.__qualname__}.{self.name}"

    def bind(self, view: type[View], /) -> Self:
        return replace(self, owner=view)  # ty: ignore[invalid-argument-type]

    def __call__(self, view: S, /, *args: P.args, **kwargs: P.kwargs) -> R:
        # `Base.users(self)`, as with a plain method
        return self.func(view, *args, **kwargs)

    @overload
    def __get__(self, instance: None, owner: type[Any], /) -> Self: ...

    @overload
    def __get__(self, instance: S, owner: type[Any], /) -> Callable[P, R]: ...

    def __get__(self, instance: S | None, owner: type[Any], /) -> Self | Callable[P, R]:
        if instance is not None:
            return MethodType(self.func, instance)

        # inherited as is: `Sub.users` is the subclass's, bound to it
        members = owner.__dict__.get(self.collected_in)
        return members.get(self.name, self) if members else self


def bind_members[M: ViewMember[Any, ..., Any]](view: type[View], members: dict[str, M], /) -> dict[str, M]:
    members = {name: member.bind(view) for name, member in members.items()}

    # only what the view defines itself: an inherited one rebinds as it is read
    for name, member in members.items():
        if name in vars(view):
            setattr(view, name, member)

    return members


__all__ = [
    "ViewMember",
    "bind_members",
]

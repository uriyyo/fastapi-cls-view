from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from types import FunctionType
from typing import TYPE_CHECKING, Any, Concatenate, final

from fastapi_cls_view._internal.inherit import collect_inherited, overrider
from fastapi_cls_view._internal.member import ViewMember, bind_members
from fastapi_cls_view.errors import DecoratorUsageError, UnknownDependencyError

from .binding import bind_view

if TYPE_CHECKING:
    from fastapi_cls_view.views import View


@final
@dataclass(frozen=True, eq=False)
class ViewDependency[S, **P, R](ViewMember[S, P, R]):
    """A view method used as a dependency: `Depends(user)`, resolved once per request, with the view."""

    collected_in = "__view_dependencies__"

    name: str
    func: Callable[Concatenate[S, P], R]
    owner: type[View] | None = None

    @cached_property
    def dependency(self) -> Callable[..., Any]:
        """What FastAPI calls - one per view, as it caches a dependency by it: `Depends(Users.user.dependency)`."""

        return bind_view(self.func, self.view).wrap(self.func)

    def dependency_for(self, view: type[View], /) -> Callable[..., Any]:
        # in the view's own class body, or inherited: the view's own, overrides included
        if self.owner is not None and not issubclass(view, self.owner):
            return self.dependency

        try:
            return view.__view_dependencies__[self.name].dependency
        except KeyError:
            raise UnknownDependencyError(view, self.name) from None

    @property
    def __signature__(self) -> Any:
        # FastAPI was handed the method itself, outside any view
        raise DecoratorUsageError(f"outside a view, depend on `{self.qualname}.dependency`, not the method")


def dependency[S, **P, R](func: Callable[Concatenate[S, P], R], /) -> ViewDependency[S, P, R]:
    match func:
        case FunctionType(__name__=name):
            return ViewDependency(name, func)
        case _:
            raise DecoratorUsageError(f"dependency() decorates a method of a view, got {func!r}")


_override = overrider("dependency", "func", (FunctionType,))


def collect_dependencies(view: type[View], /) -> dict[str, ViewDependency[Any, ..., Any]]:
    return bind_members(view, collect_inherited(view, ViewDependency, _override))


__all__ = [
    "ViewDependency",
    "collect_dependencies",
    "dependency",
]

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, ForwardRef, get_args

from fastapi import Depends
from fastapi_injected.ext import is_async_callable
from fastapi_injected.signature import Signature
from starlette.concurrency import run_in_threadpool

from fastapi_cls_view._internal.view.fields import mro_namespace
from fastapi_cls_view._internal.view.generics import substitute_signature, typevars_for
from fastapi_cls_view._internal.view.markers import bind_signature
from fastapi_cls_view.errors import MissingSelfError, ViewNotReadyError

if TYPE_CHECKING:
    from fastapi_cls_view.views import View


def _unresolved(annotation: Any, /) -> str | None:
    match annotation:
        case ForwardRef(__forward_arg__=name) | str(name):
            return name
        case _:
            return next((name for arg in get_args(annotation) if (name := _unresolved(arg)) is not None), None)


def bind_view(func: Callable[..., Any], view: type[View], /, *, include_return: bool = False) -> Signature:
    sign = Signature.of(func, localns=mro_namespace(view), include_return=include_return)
    sign = substitute_signature(sign, typevars_for(view, func))
    sign = Signature.of(bind_signature(sign, view))

    try:
        self_name = next(iter(sign.parameters))
    except StopIteration:
        raise MissingSelfError(func) from None

    for annotation in (*(param.annotation for param in sign.parameters.values()), sign.return_annotation):
        if (name := _unresolved(annotation)) is not None:
            raise ViewNotReadyError(view, NameError(f"name {name!r} is not defined", name=name))

    return sign.update(
        self_name,
        # FastAPI builds the view once per request, from `View.__signature__`
        annotation=Annotated[Any, Depends(view)],
        kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
        default=inspect.Parameter.empty,
    )


async def run(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    if is_async_callable(func):
        return await func(*args, **kwargs)

    return await run_in_threadpool(func, *args, **kwargs)


def split_view(self_name: str, args: tuple[Any, ...], kwargs: dict[str, Any], /) -> tuple[Any, tuple[Any, ...]]:
    # FastAPI passes the view by its parameter name, calls by hand pass it first
    if self_name in kwargs:
        return kwargs.pop(self_name), args

    return args[0], args[1:]


__all__ = [
    "bind_view",
    "run",
    "split_view",
]

from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin

from fastapi import params

from fastapi_cls_view.types import BindsToView

if TYPE_CHECKING:
    from fastapi_cls_view.views import View


# `Depends(user)` of a `@dependency` method: the callable of the view's own


def bind_marker(value: Any, view: type[View], /) -> Any:
    match value:
        case params.Depends(dependency=BindsToView() as dependency):
            return replace(value, dependency=dependency.dependency_for(view))
        case _:
            return value


def bind_markers(markers: Iterable[Any], view: type[View], /) -> list[Any]:
    return [bind_marker(marker, view) for marker in markers]


def bind_annotation(annotation: Any, view: type[View], /) -> Any:
    if get_origin(annotation) is not Annotated:
        return annotation

    tp, *metadata = get_args(annotation)
    bound = bind_markers(metadata, view)

    if all(new is old for new, old in zip(bound, metadata, strict=True)):
        return annotation

    return Annotated[tp, *bound]


def view_markers(annotation: Any, default: Any, /) -> list[BindsToView]:
    # the `@dependency` methods a parameter depends on
    metadata = get_args(annotation)[1:] if get_origin(annotation) is Annotated else ()

    return [
        marker.dependency
        for marker in (*metadata, default)
        if isinstance(marker, params.Depends) and isinstance(marker.dependency, BindsToView)
    ]


def bind_signature(sign: inspect.Signature, view: type[View], /) -> inspect.Signature:
    return sign.replace(
        parameters=[
            param.replace(annotation=bind_annotation(param.annotation, view), default=bind_marker(param.default, view))
            for param in sign.parameters.values()
        ],
    )


__all__ = [
    "bind_annotation",
    "bind_marker",
    "bind_markers",
    "bind_signature",
    "view_markers",
]

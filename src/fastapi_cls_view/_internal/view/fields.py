import dataclasses
import inspect
import sys
import typing
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, final

from fastapi import Depends, params
from pydantic._internal._model_construction import unpack_lenient_weakvaluedict
from pydantic._internal._namespace_utils import get_module_ns_of
from pydantic._internal._typing_extra import eval_type

from fastapi_cls_view.errors import ViewDependencyCycleError, ViewNotReadyError
from fastapi_cls_view.types import ViewFields

from .generics import substitute, type_params_namespace, typevars_of
from .markers import bind_annotation, bind_marker, view_markers


@final
@dataclass(frozen=True, slots=True)
class ViewField:
    name: str
    owner: type[Any]
    default: Any = inspect.Parameter.empty
    has_factory: bool = False


def own_annotations(cls: type[Any], /) -> dict[str, Any]:
    if sys.version_info >= (3, 14):  # pragma: no cover
        import annotationlib  # noqa: PLC0415

        return annotationlib.get_annotations(cls, format=annotationlib.Format.FORWARDREF)

    return inspect.get_annotations(cls)


def is_classvar(annotation: Any, /) -> bool:
    if isinstance(annotation, str):
        return annotation.startswith(("ClassVar", "typing.ClassVar"))

    return annotation is ClassVar or typing.get_origin(annotation) is ClassVar


def is_private(name: str, /) -> bool:
    return name.startswith("_") and not name.startswith("__")


def hide_private_fields(cls: type[Any], /) -> None:
    for name, annotation in own_annotations(cls).items():
        if not is_private(name) or is_classvar(annotation):
            continue

        match vars(cls).get(name, dataclasses.MISSING):
            case dataclasses.Field() as spec:
                spec.init = False
            case value:
                setattr(cls, name, dataclasses.field(init=False, default=value))


def parent_namespace(depth: int, /) -> dict[str, Any] | None:
    frame = sys._getframe(depth + 1)  # noqa: SLF001

    while frame is not None:
        match frame.f_code.co_name:
            # a subclass calling `super().__init_subclass__()`
            case "__init_subclass__":
                frame = frame.f_back
            # the scope Python opens for the type parameters of `class View[T]`
            case name if name.startswith("<generic parameters of"):
                frame = frame.f_back
            case _:
                break

    if frame is None or frame.f_locals is frame.f_globals:
        return None

    return {**frame.f_locals}


def _default(field: dataclasses.Field[Any], /) -> Any:
    match field.default:
        case dataclasses.MISSING:
            return inspect.Parameter.empty
        case default:
            return default


def collect_fields(cls: type[Any], /) -> ViewFields:
    owners = {name: base for base in reversed(cls.__mro__) for name in own_annotations(base)}

    return {
        field.name: ViewField(
            name=field.name,
            owner=owners[field.name],
            default=_default(field),
            has_factory=field.default_factory is not dataclasses.MISSING,
        )
        for field in dataclasses.fields(cls)
        if field.init
    }


def type_namespace(cls: type[Any], /) -> dict[str, Any]:
    # pydantic's order
    return {
        **(unpack_lenient_weakvaluedict(cls.__dict__.get("__view_parent_namespace__")) or {}),
        **cls.__dict__.get("__view_types_namespace__", {}),
        **type_params_namespace(cls),
        **vars(cls),
        cls.__name__: cls,
    }


def mro_namespace(cls: type[Any], /) -> dict[str, Any]:
    # an inherited method sees the names of the class it was defined in, the subclass's win
    namespace: dict[str, Any] = {}

    for base in reversed(cls.__mro__[:-1]):
        namespace.update(type_namespace(base))

    return namespace


def resolve_annotation(field: ViewField, view: type[Any], /) -> Any:
    annotation = own_annotations(field.owner)[field.name]
    localns = {**type_namespace(field.owner), **type_namespace(view)}

    try:
        return eval_type(annotation, get_module_ns_of(field.owner), localns)
    except NameError as error:
        # a `TYPE_CHECKING`-only type of `= Depends(get_service)`: the dependency is all FastAPI needs
        if isinstance(field.default, params.Depends) and field.default.dependency is not None:
            return Any

        raise ViewNotReadyError(view, error) from error


def _view_dependency(annotation: Any, default: Any, view_base: type[Any], /) -> Any:
    # `parent: Users`: the other view, built once for the request
    if default is inspect.Parameter.empty and isinstance(annotation, type) and issubclass(annotation, view_base):
        return Annotated[annotation, Depends(annotation)]

    return annotation


def _field_parameter(field: ViewField, cls: type[Any], view_base: type[Any], /) -> inspect.Parameter:
    annotation = substitute(resolve_annotation(field, cls), typevars_of(cls, field.owner))
    annotation = _view_dependency(annotation, field.default, view_base)

    # the view's own would need the view it is building
    for marker in view_markers(annotation, field.default):
        if marker.owner is None or issubclass(cls, marker.owner):
            raise ViewDependencyCycleError(cls, field.name)

    return inspect.Parameter(
        field.name,
        inspect.Parameter.KEYWORD_ONLY,
        annotation=bind_annotation(annotation, cls),
        default=bind_marker(field.default, cls),
    )


def view_signature(cls: type[Any], /, *, view_base: type[Any]) -> inspect.Signature:
    return inspect.Signature(
        [
            _field_parameter(field, cls, view_base)
            for field in cls.__view_fields__.values()
            # FastAPI would hand every request the same object
            if not field.has_factory
        ],
        return_annotation=cls,
    )


__all__ = [
    "ViewField",
    "collect_fields",
    "hide_private_fields",
    "mro_namespace",
    "parent_namespace",
    "resolve_annotation",
    "type_namespace",
    "view_signature",
]

import inspect
from typing import Any, Generic, get_origin

from pydantic._internal._generics import get_standard_typevars_map, replace_types

from fastapi_cls_view.types import TypeVarMap


def typevar_maps(cls: type[Any], /) -> dict[type[Any], TypeVarMap]:
    # per class: one `TypeVar` may be a parameter of several, `class Nested(CRUD[list[T]], Generic[T])`
    maps: dict[type[Any], TypeVarMap] = {}

    def visit(klass: type[Any], mapping: TypeVarMap, /) -> None:
        if klass in maps:
            return

        maps[klass] = mapping

        # without its own `__orig_bases__`, a class would report its parents'
        for base in klass.__dict__.get("__orig_bases__", klass.__bases__):
            match get_origin(base) or base:
                case type() as origin if origin is not Generic:
                    params = get_standard_typevars_map(base) or {}
                    visit(origin, {param: replace_types(arg, mapping) for param, arg in params.items()})

    visit(cls, {})
    return maps


def typevars_of(view: type[Any], owner: type[Any] | None, /) -> TypeVarMap:
    # the substitution for what `owner` defines, as `view` fills its parameters in
    maps: dict[type[Any], TypeVarMap] = view.__view_typevars__

    if owner is not None and owner in maps:
        return maps[owner]

    return {param: arg for mapping in maps.values() for param, arg in mapping.items()}


def _defining_class(view: type[Any], func: Any, /) -> type[Any] | None:
    # the class whose namespace holds `func` - itself, or a route or hook made of it
    for base in view.__mro__:
        for value in vars(base).values():
            if value is func or getattr(value, "func", None) is func or getattr(value, "__func__", None) is func:
                return base

    return None


def typevars_for(view: type[Any], func: Any, /) -> TypeVarMap:
    return typevars_of(view, _defining_class(view, func))


def substitute(tp: Any, mapping: TypeVarMap, /) -> Any:
    return replace_types(tp, mapping) if mapping else tp


def substitute_signature(sign: inspect.Signature, mapping: TypeVarMap, /) -> inspect.Signature:
    if not mapping:
        return sign

    return sign.replace(
        parameters=[
            param.replace(annotation=replace_types(param.annotation, mapping)) for param in sign.parameters.values()
        ],
        return_annotation=replace_types(sign.return_annotation, mapping),
    )


def type_params_namespace(cls: type[Any], /) -> dict[str, Any]:
    # PEP 695 type parameters live in the class scope, not the module
    return {param.__name__: param for klass in reversed(cls.__mro__) for param in getattr(klass, "__type_params__", ())}


__all__ = [
    "substitute",
    "substitute_signature",
    "type_params_namespace",
    "typevar_maps",
    "typevars_for",
    "typevars_of",
]

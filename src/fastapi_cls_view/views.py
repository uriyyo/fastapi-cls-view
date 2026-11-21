from __future__ import annotations

import inspect
import types
from collections.abc import Collection, Generator, Mapping
from contextlib import contextmanager
from dataclasses import Field, dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar, Final, Unpack, dataclass_transform, override

from fastapi import APIRouter, FastAPI
from fastapi_injected.ext import MakeDataclass
from pydantic._internal._model_construction import build_lenient_weakvaluedict
from pydantic._internal._utils import LazyClassAttribute

from fastapi_cls_view._internal.hooks.collect import check_hook_routes, collect_hooks, lifespan_hooks, view_lifespan
from fastapi_cls_view._internal.routes.dependencies import ViewDependency, collect_dependencies
from fastapi_cls_view._internal.routes.route import collect_routes
from fastapi_cls_view._internal.view.config import merge_view_config, pop_view_config
from fastapi_cls_view._internal.view.fields import (
    collect_fields,
    hide_private_fields,
    is_classvar,
    own_annotations,
    parent_namespace,
    view_signature,
)
from fastapi_cls_view._internal.view.generics import typevar_maps
from fastapi_cls_view._internal.view.markers import bind_markers

from .docs import merge_responses
from .errors import (
    ReservedNameError,
    UnknownClassKeywordsError,
    UnknownFieldError,
    UnknownRouteError,
    UnsupportedViewOptionError,
)
from .types import AnyViewRoute, TypeVarMap, ViewConfig, ViewFields, ViewHooks, ViewRoutes

_RESERVED_NAMES: Final = frozenset({"as_router", "override", "view_rebuild"})
_DATACLASS_OPTIONS: Final = frozenset(inspect.signature(dataclass).parameters) - {"cls"}


def _check_reserved_names(cls: type[View], /) -> None:
    for name in vars(cls).keys() & _RESERVED_NAMES:
        raise ReservedNameError(cls, name)

    # annotated, it would become a dataclass field
    if (annotation := own_annotations(cls).get("view_config")) is not None and not is_classvar(annotation):
        raise ReservedNameError(cls, "view_config")


def _complete_signature(cls: type[View], /) -> inspect.Signature:
    sign = view_signature(cls, view_base=View)

    if not cls.__dict__.get("__view_complete__", False):
        cls.__view_on_complete__()
        cls.__view_complete__ = True

    return sign


def _type_name(tp: Any, /) -> str:
    # `list[int]` and `list[str]` apart, `int | None` as written
    if isinstance(tp, type) and not getattr(tp, "__args__", None):
        return tp.__qualname__

    return repr(tp).replace("typing.", "")


def _parametrize(cls: type[View], alias: Any, /) -> type[View]:
    args = ", ".join(_type_name(arg) for arg in alias.__args__)

    def body(namespace: dict[str, Any]) -> None:
        namespace.update(
            __module__=cls.__module__,
            __qualname__=f"{cls.__qualname__}[{args}]",
            __view_origin__=cls,
        )

    return types.new_class(f"{cls.__name__}[{args}]", (alias,), exec_body=body)


def _lazy_signature(cls: type[View], /) -> Any:
    return LazyClassAttribute("__signature__", partial(_complete_signature, cls))


@dataclass_transform(kw_only_default=True, field_specifiers=(field, Field))
class View(MakeDataclass):
    __view_config__: ClassVar[ViewConfig] = {}
    __view_fields__: ClassVar[ViewFields] = {}
    __view_routes__: ClassVar[ViewRoutes] = {}
    __view_hooks__: ClassVar[ViewHooks] = {}
    __view_dependencies__: ClassVar[dict[str, ViewDependency[Any, ..., Any]]] = {}
    __view_parent_namespace__: ClassVar[dict[str, Any] | None] = None
    __view_types_namespace__: ClassVar[dict[str, Any]] = {}
    __view_typevars__: ClassVar[dict[type[Any], TypeVarMap]] = {}
    __view_complete__: ClassVar[bool] = False

    view_config: ClassVar[ViewConfig]

    __signature__: ClassVar[inspect.Signature]

    if TYPE_CHECKING:

        @override
        def __init_subclass__(cls, **kwargs: Unpack[ViewConfig]) -> None: ...

    else:

        @override
        def __init_subclass__(cls, **kwargs: Any) -> None:
            config = pop_view_config(kwargs)
            options = {key: kwargs.pop(key) for key in [*kwargs] if key in _DATACLASS_OPTIONS}

            # `dataclass(slots=True)` makes a new class, which `__init_subclass__` can't hand back
            if unsupported := {key for key in ("slots", "weakref_slot") if options.get(key)}:
                raise UnsupportedViewOptionError(cls, unsupported)

            _check_reserved_names(cls)

            cls.__view_config__ = merge_view_config(cls.__bases__, vars(cls).get("view_config"), config)

            # made by `__class_getitem__`: no enclosing scope of its own
            if "__view_origin__" not in vars(cls):
                cls.__view_parent_namespace__ = build_lenient_weakvaluedict(parent_namespace(1))

            hide_private_fields(cls)

            # `None` until the fields are known: `dataclass()` reads the signature for `__doc__` meanwhile
            cls.__signature__ = None  # type: ignore[ty:invalid-assignment]

            # a view is a service, not a value: it keeps the identity hash
            options.setdefault("kw_only", True)
            options.setdefault("eq", False)
            super().__init_subclass__(**options)

            cls.__view_fields__ = collect_fields(cls)
            cls.__view_routes__ = collect_routes(cls)
            cls.__view_hooks__ = collect_hooks(cls)
            cls.__view_dependencies__ = collect_dependencies(cls)
            cls.__view_typevars__ = typevar_maps(cls)
            cls.__signature__ = _lazy_signature(cls)

            # the parent's hook, as `__init_subclass__` runs
            super(cls, cls).__view_init_subclass__(**kwargs)

        def __class_getitem__(cls, params: Any) -> Any:
            alias = super().__class_getitem__(params)  # ty: ignore[missing-attribute]

            # still generic, `class Users[T](CRUD[T])`: the subclass substitutes it
            if alias.__parameters__:
                return alias

            # concrete, `CRUD[User].as_router()`: a subclass, so the types reach the routes
            cache: dict[Any, type[View]] = cls.__dict__.get("__view_parametrized__") or {}
            cls.__view_parametrized__ = cache

            try:
                return cache[alias]
            except KeyError:
                pass
            except TypeError:  # unhashable arguments: built each time
                return _parametrize(cls, alias)

            cache[alias] = _parametrize(cls, alias)
            return cache[alias]

    @classmethod
    def __view_init_subclass__(cls, **kwargs: Any) -> None:
        """Called for each subclass once it is collected, with the class keywords left over."""

        if kwargs:
            raise UnknownClassKeywordsError(cls, {*kwargs})

    @classmethod
    def __view_on_complete__(cls) -> None:
        """Called once the view's fields are resolved."""

    @classmethod
    def __view_route_name__(cls, route: AnyViewRoute, /) -> str:
        """The route name for `url_path_for()`, unless the route decorator sets `name=` - `Users.list`."""

        return f"{cls.__qualname__}.{route.name}"

    @classmethod
    def __view_operation_id__(cls, route: AnyViewRoute, method: str, path: str, /) -> str | None:
        """The OpenAPI operation id - `None` leaves it to `generate_unique_id_function`."""

        return None

    @classmethod
    def view_rebuild(cls, **types_namespace: Any) -> None:
        """Resolve the annotations again, with the names passed here."""

        cls.__view_types_namespace__ = {
            **cls.__dict__.get("__view_types_namespace__", {}),
            **types_namespace,
        }

        cls.__signature__ = _lazy_signature(cls)

        for route in cls.__view_routes__.values():
            route.__dict__.pop("endpoint", None)
            route.__dict__.pop("hook_dependencies", None)

        for dep in cls.__view_dependencies__.values():
            dep.__dict__.pop("dependency", None)

        # a name still missing fails here
        _ = cls.__signature__

        for route in cls.__view_routes__.values():
            _ = route.endpoint, route.hook_dependencies

        for dep in cls.__view_dependencies__.values():
            _ = dep.dependency

    @classmethod
    @contextmanager
    def override(cls, app: FastAPI, /, **fields: Any) -> Generator[None]:
        """Serve the view with these field values - for tests: `with Users.override(app, repo=FakeRepo()):`."""

        sign = cls.__signature__

        if unknown := {*fields} - {*cls.__view_fields__}:
            raise UnknownFieldError(cls, unknown)

        def view(**resolved: Any) -> Any:
            return cls(**resolved, **fields)

        # FastAPI resolves only the fields left
        view.__signature__ = sign.replace(  # ty: ignore[missing-attribute]
            parameters=[param for name, param in sign.parameters.items() if name not in fields],
        )

        overrides = app.dependency_overrides
        previous = overrides.get(cls)
        overrides[cls] = view

        try:
            yield
        finally:
            if previous is None:
                overrides.pop(cls, None)
            else:
                overrides[cls] = previous

    @classmethod
    def as_router(
        cls,
        *,
        include: Collection[str] | None = None,
        exclude: Collection[str] = (),
        **overrides: Unpack[ViewConfig],
    ) -> APIRouter:
        """The view's router: `include` and `exclude` pick its routes by name, as pydantic's `model_dump()`."""

        if unknown := ({*(include or ())} | {*exclude}) - {*cls.__view_routes__}:
            raise UnknownRouteError(cls, unknown)

        config: dict[str, Any] = {**cls.__view_config__, **overrides}

        # declared as a `Sequence` so `tags=["users"]` type checks - `APIRouter` concatenates them as lists
        if (tags := config.get("tags")) is not None:
            config["tags"] = [*tags]

        match config.get("responses"):
            case None | Mapping():
                pass
            case docs:
                config["responses"] = merge_responses(doc.to_fastapi() for doc in docs)

        if dependencies := config.get("dependencies"):
            config["dependencies"] = bind_markers(dependencies, cls)

        if lifespans := lifespan_hooks([*cls.__view_hooks__.values()]):
            config["lifespan"] = view_lifespan(cls, lifespans)

        check_hook_routes(cls)
        router = APIRouter(**config)

        for name, route in cls.__view_routes__.items():
            if (include is None or name in include) and name not in exclude:
                route.register(router)

        return router


__all__ = [
    "View",
]

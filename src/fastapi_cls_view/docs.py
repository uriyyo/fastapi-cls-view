from collections.abc import Callable, Iterable, Mapping
from dataclasses import KW_ONLY, dataclass, field, replace
from http import HTTPStatus
from typing import Any, Self, final

from fastapi.encoders import jsonable_encoder
from pydantic import TypeAdapter

from .errors import DecoratorUsageError
from .types import Responses


@final
@dataclass(frozen=True, slots=True)
class Example:
    value: Any
    summary: str | None = None
    description: str | None = None

    def to_openapi(self) -> dict[str, Any]:
        example = {"summary": self.summary, "description": self.description, "value": jsonable_encoder(self.value)}
        return {key: value for key, value in example.items() if value is not None}


@final
@dataclass(frozen=True, slots=True)
class ResponseHeader:
    description: str | None = None
    type: Any = str

    def to_openapi(self) -> dict[str, Any]:
        header: dict[str, Any] = {"schema": TypeAdapter(self.type).json_schema()}

        if self.description:
            header["description"] = self.description

        return header


@final
@dataclass(frozen=True, slots=True)
class ResponseDoc:
    status: int | str
    model: Any = None
    description: str | None = None
    _: KW_ONLY
    headers: Mapping[str, ResponseHeader | str] = field(default_factory=dict)
    examples: Mapping[str, Example | Any] = field(default_factory=dict)
    media_type: str = "application/json"

    @property
    def default_description(self) -> str:
        try:
            return HTTPStatus(int(self.status)).phrase
        except ValueError:
            return "Response"

    def to_fastapi(self) -> Responses:
        response: dict[str, Any] = {"description": self.description or self.default_description}

        if self.model is not None:
            response["model"] = self.model

        if self.headers:
            response["headers"] = {name: _header(header).to_openapi() for name, header in self.headers.items()}

        if self.examples:
            examples = {name: _example(example).to_openapi() for name, example in self.examples.items()}
            response["content"] = {self.media_type: {"examples": examples}}
        elif self.media_type != "application/json":
            response["content"] = {self.media_type: {}}

        return {self.status: response}


class Documented:
    __slots__ = ()

    docs: tuple[ResponseDoc, ...]

    def with_doc(self, doc: ResponseDoc, /) -> Self:
        return replace(self, docs=(*self.docs, doc))  # ty: ignore[invalid-argument-type]


def _header(header: ResponseHeader | str, /) -> ResponseHeader:
    match header:
        case ResponseHeader():
            return header
        case description:
            return ResponseHeader(description)


def _example(example: Example | Any, /) -> Example:
    match example:
        case Example():
            return example
        case value:
            return Example(value)


def responds[T](  # noqa: PLR0913 - one argument per `ResponseDoc` field
    status: int | str,
    model: Any = None,
    description: str | None = None,
    *,
    headers: Mapping[str, ResponseHeader | str] | None = None,
    examples: Mapping[str, Example | Any] | None = None,
    media_type: str = "application/json",
) -> Callable[[T], T]:
    """Document a response of a route, or of every route a hook applies to - a view takes `responses=`."""

    doc = ResponseDoc(status, model, description, headers=headers or {}, examples=examples or {}, media_type=media_type)

    def decorator(target: T, /) -> T:
        match target:
            case Documented():
                return target.with_doc(doc)
            case _:
                raise DecoratorUsageError(
                    f"responds() documents a route or a hook - put it above their decorator, got {target!r}",
                )

    return decorator


def merge_responses(responses: Iterable[Mapping[int | str, Mapping[str, Any]]], /) -> Responses:
    merged: Responses = {}

    for source in responses:
        for status, response in source.items():
            current = merged.setdefault(status, {})

            for key, value in response.items():
                match key, current.get(key):
                    case "headers" | "content", dict() as previous:
                        current[key] = {**previous, **value}
                    case _:
                        current[key] = value

    return merged


__all__ = [
    "Documented",
    "Example",
    "ResponseDoc",
    "ResponseHeader",
    "merge_responses",
    "responds",
]

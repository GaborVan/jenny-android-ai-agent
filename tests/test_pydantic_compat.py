"""Tests for the Pydantic-compatible wrapper."""

from __future__ import annotations

from typing import Any, Literal

import pytest

from jenny.pydantic_compat import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
    to_camel,
)


class SimpleModel(BaseModel):
    name: str
    count: int = 0


def test_basic_model_creation():
    m = SimpleModel(name="test", count=5)
    assert m.name == "test"
    assert m.count == 5


def test_model_validate_defaults():
    m = SimpleModel.model_validate({"name": "hello"})
    assert m.name == "hello"
    assert m.count == 0


def test_model_validate_missing_required():
    with pytest.raises(ValidationError):
        SimpleModel.model_validate({})


def test_model_dump():
    m = SimpleModel(name="hello", count=3)
    assert m.model_dump() == {"name": "hello", "count": 3}


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    first_name: str
    last_name: str = ""


def test_alias_generator_camel_case():
    m = CamelModel.model_validate({"firstName": "Ada", "lastName": "Lovelace"})
    assert m.first_name == "Ada"
    assert m.last_name == "Lovelace"


def test_populate_by_name_snake_case():
    m = CamelModel.model_validate({"first_name": "Grace", "last_name": "Hopper"})
    assert m.first_name == "Grace"
    assert m.last_name == "Hopper"


def test_model_dump_by_alias():
    m = CamelModel(first_name="Alan", last_name="Turing")
    assert m.model_dump(by_alias=True) == {"firstName": "Alan", "lastName": "Turing"}


def test_to_camel_helper():
    assert to_camel("hello_world") == "helloWorld"
    assert to_camel("simple") == "simple"
    assert to_camel("a_b_c") == "aBC"


class AliasModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    field_a: str = Field(validation_alias=AliasChoices("fieldA", "a"))
    field_b: str = Field(serialization_alias="fieldB")


def test_alias_choices_validation():
    m1 = AliasModel.model_validate({"fieldA": "x", "field_b": "y"})
    assert m1.field_a == "x"
    m2 = AliasModel.model_validate({"a": "y", "field_b": "z"})
    assert m2.field_a == "y"


def test_serialization_alias():
    m = AliasModel(field_a="x", field_b="y")
    assert m.model_dump(by_alias=True) == {"field_a": "x", "fieldB": "y"}


class ConstrainedModel(BaseModel):
    age: int = Field(default=0, ge=0, le=120)


def test_numeric_constraints():
    m = ConstrainedModel.model_validate({"age": 25})
    assert m.age == 25


def test_constraint_violations():
    with pytest.raises(ValidationError):
        ConstrainedModel.model_validate({"age": -1})
    with pytest.raises(ValidationError):
        ConstrainedModel.model_validate({"age": 121})


class ValidatedModel(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def ensure_leading_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError('path must start with "/"')
        return value


def test_field_validator():
    m = ValidatedModel.model_validate({"path": "/ok"})
    assert m.path == "/ok"


def test_field_validator_failure():
    with pytest.raises(ValidationError):
        ValidatedModel.model_validate({"path": "bad"})


class BeforeValidatorModel(BaseModel):
    values: list[int]

    @model_validator(mode="before")
    @classmethod
    def wrap_single_value(cls, data: dict[str, Any]) -> dict[str, Any]:
        if isinstance(data.get("values"), int):
            data["values"] = [data["values"]]
        return data


def test_model_validator_before():
    m = BeforeValidatorModel.model_validate({"values": 5})
    assert m.values == [5]


class AfterValidatorModel(BaseModel):
    a: int
    b: int

    @model_validator(mode="after")
    def b_must_be_greater(self) -> "AfterValidatorModel":
        if self.b <= self.a:
            raise ValueError("b must be greater than a")
        return self


def test_model_validator_after():
    m = AfterValidatorModel.model_validate({"a": 1, "b": 2})
    assert m.a == 1
    assert m.b == 2


def test_model_validator_after_failure():
    with pytest.raises(ValidationError):
        AfterValidatorModel.model_validate({"a": 2, "b": 1})


class ExtraAllowModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    known: str = "default"


def test_extra_allow():
    m = ExtraAllowModel.model_validate({"known": "x", "extra_field": 42})
    assert m.known == "x"
    assert m.extra_field == 42
    assert m.__pydantic_extra__ == {"extra_field": 42}
    dumped = m.model_dump()
    assert dumped["known"] == "x"
    assert dumped["extra_field"] == 42


def test_extra_allow_setattr():
    m = ExtraAllowModel(known="x")
    m.other = "y"
    assert m.other == "y"
    assert m.__pydantic_extra__ == {"other": "y"}


class ExtraForbidModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known: str = "default"


def test_extra_forbid():
    with pytest.raises(ValidationError):
        ExtraForbidModel.model_validate({"known": "x", "extra": 1})


def test_extra_forbid_setattr():
    m = ExtraForbidModel()
    with pytest.raises(ValidationError):
        m.extra = 1


class NestedChild(BaseModel):
    value: int


class NestedParent(BaseModel):
    child: NestedChild
    children: list[NestedChild] = Field(default_factory=list)


def test_nested_model():
    m = NestedParent.model_validate({
        "child": {"value": 1},
        "children": [{"value": 2}, {"value": 3}],
    })
    assert isinstance(m.child, NestedChild)
    assert m.child.value == 1
    assert len(m.children) == 2
    assert m.children[0].value == 2


def test_nested_model_dump():
    m = NestedParent.model_validate({
        "child": {"value": 1},
        "children": [{"value": 2}],
    })
    dumped = m.model_dump()
    assert dumped == {"child": {"value": 1}, "children": [{"value": 2}]}


class InlineConfig(BaseModel):
    name: str
    value: int


FallbackCandidate = str | InlineConfig


class UnionModel(BaseModel):
    fallback: FallbackCandidate = "default"
    fallbacks: list[FallbackCandidate] = Field(default_factory=list)


def test_union_discriminated_dict():
    m = UnionModel.model_validate({"fallback": {"name": "inline", "value": 5}})
    assert isinstance(m.fallback, InlineConfig)
    assert m.fallback.name == "inline"


def test_union_discriminated_str():
    m = UnionModel.model_validate({"fallback": "named"})
    assert m.fallback == "named"


def test_union_list():
    m = UnionModel.model_validate({
        "fallbacks": ["named", {"name": "inline", "value": 1}],
    })
    assert m.fallbacks[0] == "named"
    assert isinstance(m.fallbacks[1], InlineConfig)


class LiteralModel(BaseModel):
    mode: Literal["a", "b"] = "a"


def test_literal_validation():
    m = LiteralModel.model_validate({"mode": "b"})
    assert m.mode == "b"
    with pytest.raises(ValidationError):
        LiteralModel.model_validate({"mode": "c"})


class DefaultFactoryModel(BaseModel):
    items: list[int] = Field(default_factory=list)


def test_default_factory():
    m1 = DefaultFactoryModel.model_validate({})
    m2 = DefaultFactoryModel.model_validate({})
    m1.items.append(1)
    assert m1.items == [1]
    assert m2.items == []


class ExcludeModel(BaseModel):
    public: str = ""
    secret: str = Field(default="", exclude=True)


def test_exclude_field():
    m = ExcludeModel(public="x", secret="y")
    assert m.model_dump() == {"public": "x"}


class ForwardModel(BaseModel):
    child: "ForwardChild | None" = None


class ForwardChild(BaseModel):
    name: str


def test_model_rebuild_forward_ref():
    assert not ForwardModel.__pydantic_complete__
    ForwardModel.model_rebuild()
    assert ForwardModel.__pydantic_complete__
    m = ForwardModel.model_validate({"child": {"name": "kid"}})
    assert isinstance(m.child, ForwardChild)
    assert m.child.name == "kid"


def test_model_copy():
    m = SimpleModel(name="orig", count=1)
    copy = m.model_copy(update={"count": 2})
    assert copy.name == "orig"
    assert copy.count == 2


def test_validation_error_message():
    err = ValidationError("something failed")
    assert str(err) == "something failed"
    assert err.errors == [{"msg": "something failed", "type": "value_error"}]


def test_validation_error_list():
    err = ValidationError([{"msg": "a", "type": "x"}, {"msg": "b", "type": "y"}])
    assert "a; b" in str(err)
    assert len(err.errors) == 2


def test_config_inheritance():
    class Base(BaseModel):
        model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    class Child(Base):
        model_config = ConfigDict(extra="allow")
        field_name: str

    m = Child.model_validate({"fieldName": "x", "extra": 1})
    assert m.field_name == "x"
    assert m.extra == 1


def test_int_for_float():
    class Model(BaseModel):
        value: float

    m = Model.model_validate({"value": 5})
    assert m.value == 5.0
    assert isinstance(m.value, float)


def test_bool_rejected_for_int():
    class Model(BaseModel):
        value: int

    with pytest.raises(ValidationError):
        Model.model_validate({"value": True})


def test_invalid_type():
    with pytest.raises(ValidationError):
        SimpleModel.model_validate({"name": 123})


def test_non_dict_input():
    with pytest.raises(ValidationError):
        SimpleModel.model_validate("not a dict")

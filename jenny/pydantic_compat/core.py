"""Pydantic-compatible BaseModel and BaseSettings implemented with stdlib dataclasses.

Perché esiste: il target di runtime è Android/Chaquopy, dove il vero Pydantic v2
non è installabile perché dipende da ``pydantic-core`` (estensione nativa in Rust).
Questo modulo reimplementa in sola stdlib il sottoinsieme dell'API Pydantic che il
progetto usa davvero.

Contratto (sottoinsieme supportato): ``BaseModel``/``BaseSettings``, ``Field`` con
default/alias/``AliasChoices``, ``model_config``, validazione dei tipi comuni
(scalari, ``Optional``/``Union``, ``Literal``, ``list``/``dict``, modelli annidati),
decoratori ``field_validator``/``model_validator``, ``model_dump``
e ``model_validate``. Superficie volutamente minima: solo ciò che ``config/`` e
``tools/base.py`` richiedono. Non estendere per emulare feature Pydantic non usate.

Il contratto è fissato dai test in ``tests/test_pydantic_compat.py`` (~42 test), che
valgono come specifica di riferimento di ciò che questo shim deve garantire.
"""

from __future__ import annotations

import dataclasses
import types
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import (
    Any,
    Callable,
    ClassVar,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from jenny.pydantic_compat.errors import ValidationError
from jenny.pydantic_compat.fields import MISSING, AliasChoices, FieldInfo

_RECOGNIZED_ORIGINS = (Union, types.UnionType)


def _unwrap_function(fn: Any) -> Any:
    """Return the raw function wrapped by classmethod, if applicable."""
    return fn.__func__ if isinstance(fn, classmethod) else fn


def _merge_config(bases: tuple[type, ...], own: dict[str, Any] | None) -> dict[str, Any]:
    """Merge inherited model_config dicts with the subclass's own config."""
    merged: dict[str, Any] = {}
    for base in bases:
        merged.update(getattr(base, "__pydantic_config__", {}) or {})
    if own:
        merged.update(own)
    return merged


def _collect_inherited(
    bases: tuple[type, ...],
    attr: str,
) -> list[Any]:
    """Collect validator lists from base classes."""
    collected: list[Any] = []
    for base in bases:
        collected.extend(getattr(base, attr, []) or [])
    return collected


def _build_field_info(
    name: str,
    annotation: Any,
    default: Any,
    alias_generator: Callable[[str], str] | None,
) -> FieldInfo:
    """Create a FieldInfo from a class field declaration."""
    if isinstance(default, FieldInfo):
        finfo = dataclasses.replace(
            default,
            name=name,
            annotation=annotation,
        )
    else:
        finfo = FieldInfo(name=name, annotation=annotation, default=default)

    if finfo.alias is None and alias_generator is not None:
        finfo.alias = alias_generator(name)
    return finfo


def _model_dataclass_defaults(
    model_fields: dict[str, FieldInfo],
) -> dict[str, Any]:
    """Return namespace defaults suitable for dataclass consumption.

    ``repr=False`` must reach the generated dataclass, or it is decoration: the
    flag is how this repo keeps secrets (``api_key``, ``bot_token``, the SSH
    ``password``) out of any ``repr`` that ends up in a log line or an error
    message. Only fields that ask for it are wrapped, so every other field keeps
    the plain-value default it had before. Note that ``dataclass()`` puts the
    plain default back on the class afterwards, so nothing else changes.
    """
    defaults: dict[str, Any] = {}
    for finfo in model_fields.values():
        if finfo.default is not MISSING:
            defaults[finfo.name] = (
                finfo.default if finfo.repr else dc_field(default=finfo.default, repr=False)
            )
        elif finfo.default_factory is not None:
            defaults[finfo.name] = dc_field(
                default_factory=finfo.default_factory, repr=finfo.repr
            )
        else:
            # Required field: MISSING stays the placeholder default, but a
            # required secret must be hidden from the repr just the same.
            defaults[finfo.name] = (
                MISSING if finfo.repr else dc_field(default=MISSING, repr=False)
            )
    return defaults


def _collect_validators(
    namespace: dict[str, Any],
    model_fields: dict[str, FieldInfo],
    field_validators: list[tuple[str, Any]],
    before_validators: list[Any],
    after_validators: list[Any],
) -> None:
    """Scan namespace for decorated validators and register them."""
    for attr_name, attr_value in list(namespace.items()):
        if not (callable(attr_value) or isinstance(attr_value, classmethod)):
            continue
        fn = _unwrap_function(attr_value)
        if hasattr(fn, "__pydantic_field_validator__"):
            for field_name in fn.__pydantic_field_validator__:
                field_validators.append((field_name, attr_value))
                if field_name in model_fields:
                    model_fields[field_name].field_validators.append(attr_value)
        elif hasattr(fn, "__pydantic_model_validator__"):
            mode = fn.__pydantic_model_validator__
            if mode == "before":
                before_validators.append(attr_value)
            elif mode == "after":
                after_validators.append(attr_value)


def _inherit_model_fields(bases: tuple[type, ...]) -> dict[str, FieldInfo]:
    """Copy model_fields from base classes into a new dict."""
    inherited: dict[str, FieldInfo] = {}
    for base in bases:
        base_fields = getattr(base, "model_fields", None) or {}
        for name, finfo in base_fields.items():
            inherited[name] = dataclasses.replace(
                finfo,
                field_validators=list(finfo.field_validators),
            )
    return inherited


def _process_field_annotations(
    cls: type,
    namespace: dict[str, Any],
    alias_generator: Callable[[str], str] | None,
) -> dict[str, FieldInfo]:
    """Build model_fields from class annotations and inherited fields."""
    annotations = getattr(cls, "__annotations__", {})
    model_fields = _inherit_model_fields(cls.__bases__)

    for field_name, annotation in annotations.items():
        if field_name.startswith("_"):
            continue
        if get_origin(annotation) is ClassVar:
            continue
        default = namespace.get(field_name, MISSING)
        finfo = _build_field_info(field_name, annotation, default, alias_generator)
        if field_name in model_fields:
            base_validators = model_fields[field_name].field_validators
            finfo.field_validators = list(base_validators) + finfo.field_validators
        model_fields[field_name] = finfo
    return model_fields


def _collect_all_validators(
    bases: tuple[type, ...],
    namespace: dict[str, Any],
    model_fields: dict[str, FieldInfo],
) -> tuple[list[tuple[str, Any]], list[Any], list[Any]]:
    """Collect inherited and declared field/model validators."""
    field_validators: list[tuple[str, Any]] = _collect_inherited(
        bases, "__pydantic_field_validators__"
    )
    before_validators: list[Any] = _collect_inherited(
        bases, "__pydantic_before_validators__"
    )
    after_validators: list[Any] = _collect_inherited(
        bases, "__pydantic_after_validators__"
    )
    _collect_validators(
        namespace,
        model_fields,
        field_validators,
        before_validators,
        after_validators,
    )
    return field_validators, before_validators, after_validators


def _strip_classvar_annotations(cls: type) -> None:
    """Remove ClassVar annotations so dataclasses ignore them."""
    annotations = getattr(cls, "__annotations__", None)
    if annotations is None:
        return
    for name in list(annotations.keys()):
        if get_origin(annotations[name]) is ClassVar:
            del annotations[name]


def _set_dataclass_defaults(cls: type, model_fields: dict[str, FieldInfo]) -> None:
    """Set class attributes used as dataclass field defaults."""
    defaults = _model_dataclass_defaults(model_fields)
    for field_name, value in defaults.items():
        setattr(cls, field_name, value if value is not MISSING else MISSING)


def _set_model_metadata(
    cls: type,
    model_fields: dict[str, FieldInfo],
    field_validators: list[tuple[str, Any]],
    before_validators: list[Any],
    after_validators: list[Any],
    config: dict[str, Any],
) -> None:
    """Attach model configuration and validator metadata to the class."""
    cls.model_fields = model_fields
    cls.model_config = config
    cls.__pydantic_config__ = config
    cls.__pydantic_complete__ = False
    cls.__pydantic_rebuilt__ = False
    cls.__pydantic_field_validators__ = field_validators
    cls.__pydantic_before_validators__ = before_validators
    cls.__pydantic_after_validators__ = after_validators


class _ModelMeta(type):
    """Metaclass that turns a class into a Pydantic-compatible dataclass model."""

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> type:
        return super().__new__(mcs, name, bases, namespace)

    def __init__(
        cls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> None:
        super().__init__(name, bases, namespace)

        own_config = namespace.get("model_config")
        own_config_dict = own_config if isinstance(own_config, dict) else {}
        config = _merge_config(bases, own_config_dict)
        alias_generator = config.get("alias_generator")

        model_fields = _process_field_annotations(cls, namespace, alias_generator)
        field_validators, before_validators, after_validators = _collect_all_validators(
            bases, namespace, model_fields
        )

        _strip_classvar_annotations(cls)
        _set_dataclass_defaults(cls, model_fields)
        dataclass(cls, init=False, repr=True, eq=True, order=False, frozen=False)
        _set_model_metadata(
            cls,
            model_fields,
            field_validators,
            before_validators,
            after_validators,
            config,
        )


def _is_model_type(t: Any) -> bool:
    """Return True if t is a BaseModel subclass."""
    return isinstance(t, type) and issubclass(t, BaseModel)


def _validate_numeric(value: float, finfo: FieldInfo, name: str) -> None:
    """Apply numeric constraints to a value."""
    if finfo.ge is not None and value < finfo.ge:
        raise ValidationError(f"{name}: value {value} is less than {finfo.ge}")
    if finfo.le is not None and value > finfo.le:
        raise ValidationError(f"{name}: value {value} is greater than {finfo.le}")


def _apply_constraints(value: Any, finfo: FieldInfo, name: str) -> None:
    """Run all configured constraints for a field."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_numeric(value, finfo, name)


def _validate_origin_value(value: Any, expected_type: Any, name: str) -> Any:
    """Validate a value whose type has a generic origin."""
    origin = get_origin(expected_type)
    args = get_args(expected_type)

    if origin in _RECOGNIZED_ORIGINS:
        return _validate_union(value, args, name)
    if origin is list:
        return _validate_list(value, args[0] if args else Any, name)
    if origin is dict:
        return _validate_dict(value, args, name)
    if origin is Literal:
        if value in args:
            return value
        raise ValidationError(f"{name}: invalid literal value {value!r}")
    raise ValidationError(f"{name}: unsupported type {expected_type!r}")


def _validate_model_value(value: Any, expected_type: type[BaseModel], name: str) -> Any:
    """Validate a value against a BaseModel subclass."""
    if isinstance(value, expected_type):
        return value
    if isinstance(value, dict):
        return expected_type.model_validate(value)
    raise ValidationError(
        f"{name}: expected dict or {expected_type.__name__}, got {type(value).__name__}"
    )


def _validate_primitive_value(value: Any, expected_type: type, name: str) -> Any:
    """Validate a value against a primitive Python type."""
    if expected_type is bool:
        if isinstance(value, bool):
            return value
        raise ValidationError(f"{name}: expected bool, got {type(value).__name__}")
    if expected_type is int and isinstance(value, bool):
        raise ValidationError(f"{name}: expected int, got bool")
    if isinstance(value, expected_type):
        return value
    if expected_type is float and isinstance(value, int):
        return float(value)
    raise ValidationError(
        f"{name}: expected {expected_type.__name__}, got {type(value).__name__}"
    )


def _validate_single(value: Any, expected_type: Any, name: str) -> Any:
    """Validate and coerce a single value against an expected type."""
    if expected_type is Any:
        return value

    if get_origin(expected_type) is not None:
        return _validate_origin_value(value, expected_type, name)

    if isinstance(expected_type, str):
        raise ValidationError(f"{name}: unresolved forward reference {expected_type!r}")

    if _is_model_type(expected_type):
        return _validate_model_value(value, expected_type, name)

    if isinstance(expected_type, type):
        return _validate_primitive_value(value, expected_type, name)

    raise ValidationError(f"{name}: unsupported type {expected_type!r}")


def _validate_union(value: Any, options: tuple[Any, ...], name: str) -> Any:
    """Try each type in a union until one succeeds."""
    for option in options:
        if value is None and option is type(None):
            return None
        try:
            return _validate_single(value, option, name)
        except (ValidationError, ValueError, TypeError):
            continue
    raise ValidationError(f"{name}: no union type matched for value {value!r}")


def _validate_list(value: Any, item_type: Any, name: str) -> list[Any]:
    """Validate a list and its items."""
    if not isinstance(value, (list, tuple)):
        raise ValidationError(f"{name}: expected list, got {type(value).__name__}")
    return [_validate_single(item, item_type, name) for item in value]


def _validate_dict(value: Any, args: tuple[Any, ...], name: str) -> dict[Any, Any]:
    """Validate a dict and its values."""
    if not isinstance(value, dict):
        raise ValidationError(f"{name}: expected dict, got {type(value).__name__}")
    value_type = args[1] if len(args) > 1 else Any
    return {k: _validate_single(v, value_type, name) for k, v in value.items()}


def _resolve_input_key(cls: type[BaseModel], key: str) -> str | None:
    """Map an input key to a declared field name using aliases."""
    config = getattr(cls, "__pydantic_config__", {})
    populate_by_name = config.get("populate_by_name", False)

    for field_name, finfo in cls.model_fields.items():
        if finfo.validation_alias:
            alias = finfo.validation_alias
            if isinstance(alias, str) and key == alias:
                return field_name
            if isinstance(alias, AliasChoices) and key in alias.aliases:
                return field_name
        if finfo.alias and key == finfo.alias:
            return field_name
        if not finfo.alias and not finfo.validation_alias and key == field_name:
            return field_name
        if populate_by_name and key == field_name:
            return field_name
    return None


def _run_field_validators(
    cls: type[BaseModel],
    value: Any,
    finfo: FieldInfo,
) -> Any:
    """Execute field validators in declaration order."""
    for validator in finfo.field_validators:
        fn = _unwrap_function(validator)
        try:
            value = fn(cls, value)
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"{finfo.name}: {e}") from e
    return value


def _run_before_validators(cls: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    """Execute model validators in mode='before'."""
    for validator in cls.__pydantic_before_validators__:
        fn = _unwrap_function(validator)
        data = fn(cls, data)
        if not isinstance(data, dict):
            raise ValidationError("before validator must return a dict")
    return data


def _run_after_validators(instance: BaseModel) -> BaseModel:
    """Execute model validators in mode='after'."""
    for validator in instance.__pydantic_after_validators__:
        try:
            result = validator(instance)
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(str(e)) from e
        if isinstance(result, type(instance)):
            instance = result
    return instance


def _build_instance(cls: type[BaseModel], values: dict[str, Any], extras: dict[str, Any]) -> BaseModel:
    """Create a model instance without running __init__."""
    instance = object.__new__(cls)
    for field_name in cls.model_fields:
        setattr(instance, field_name, values[field_name])
    object.__setattr__(instance, "__pydantic_extra__", extras)
    return instance


def _normalize_input(
    cls: type[BaseModel],
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve aliases and separate known fields from extras."""
    normalized: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    extra_policy = cls.__pydantic_config__.get("extra")

    for key, value in data.items():
        field_name = _resolve_input_key(cls, key)
        if field_name is not None:
            normalized[field_name] = value
            continue
        if extra_policy == "forbid":
            raise ValidationError(f"Extra field not allowed: {key}")
        if extra_policy == "allow":
            extras[key] = value
    return normalized, extras


def _resolve_field_value(finfo: FieldInfo, normalized: dict[str, Any]) -> Any:
    """Return the effective value for a field, applying defaults."""
    value = normalized.get(finfo.name, MISSING)
    if value is MISSING:
        value = finfo.get_default()
    return value


def _validate_field_values(
    cls: type[BaseModel],
    normalized: dict[str, Any],
) -> dict[str, Any]:
    """Validate all declared fields and return the final value dict."""
    final: dict[str, Any] = {}
    for field_name, finfo in cls.model_fields.items():
        value = _resolve_field_value(finfo, normalized)
        if value is MISSING:
            raise ValidationError(f"Field required: {field_name}")

        expected = finfo.resolved_type if finfo.resolved_type is not None else finfo.annotation
        validated = _validate_single(value, expected, field_name)
        _apply_constraints(validated, finfo, field_name)
        validated = _run_field_validators(cls, validated, finfo)
        final[field_name] = validated
    return final


class BaseModel(metaclass=_ModelMeta):
    """Pydantic-compatible base model implemented with stdlib dataclasses."""

    model_fields = {}
    model_config = {}
    __pydantic_complete__ = False
    __pydantic_rebuilt__ = False
    __pydantic_extra__ = {}

    def __init__(self, **data: Any) -> None:
        instance = type(self).model_validate(data, _internal=True)
        for field_name in type(self).model_fields:
            object.__setattr__(self, field_name, getattr(instance, field_name))
        object.__setattr__(self, "__pydantic_extra__", getattr(instance, "__pydantic_extra__"))

    def __getattr__(self, name: str) -> Any:
        if name == "__pydantic_extra__":
            raise AttributeError(name)
        extras = self.__dict__.get("__pydantic_extra__", {})
        if name in extras:
            return extras[name]
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        if name in type(self).model_fields or name == "__pydantic_extra__":
            super().__setattr__(name, value)
            return
        extra = type(self).__pydantic_config__.get("extra")
        if extra == "allow":
            self.__pydantic_extra__[name] = value
        elif extra == "forbid":
            raise ValidationError(f"Extra field not allowed: {name}")
        else:
            super().__setattr__(name, value)

    @classmethod
    def model_rebuild(
        cls,
        *,
        force: bool = False,
        raise_errors: bool = False,
    ) -> bool:
        """Resolve forward references in type annotations."""
        if cls.__pydantic_rebuilt__ and not force:
            return True
        try:
            hints = get_type_hints(cls)
            for field_name, finfo in cls.model_fields.items():
                finfo.resolved_type = hints.get(field_name, finfo.annotation)
            cls.__pydantic_complete__ = True
            cls.__pydantic_rebuilt__ = True
            return True
        except Exception:
            cls.__pydantic_complete__ = False
            if raise_errors:
                raise
            return False

    @classmethod
    def model_validate(cls, data: dict[str, Any], _internal: bool = False) -> BaseModel:
        """Validate a dict and return a model instance."""
        if not isinstance(data, dict):
            raise ValidationError(f"Expected dict, got {type(data).__name__}")

        if not cls.__pydantic_rebuilt__:
            cls.model_rebuild(raise_errors=False)

        data = _run_before_validators(cls, data)
        normalized, extras = _normalize_input(cls, data)
        final = _validate_field_values(cls, normalized)
        instance = _build_instance(cls, final, extras)
        instance = _run_after_validators(instance)
        return instance

    def model_dump(self, *, by_alias: bool = False, mode: str = "python") -> dict[str, Any]:
        """Serialize the model to a dict."""
        result: dict[str, Any] = {}
        for field_name, finfo in type(self).model_fields.items():
            if finfo.exclude:
                continue
            key = _serialization_key(finfo, by_alias)
            value = getattr(self, field_name)
            result[key] = _dump_value(value, by_alias=by_alias, mode=mode)

        extras = self.__dict__.get("__pydantic_extra__", {})
        for key, value in extras.items():
            result[key] = _dump_value(value, by_alias=by_alias, mode=mode)
        return result

    def model_copy(self, *, update: dict[str, Any] | None = None) -> BaseModel:
        """Return a shallow copy, optionally updating fields."""
        data = self.model_dump()
        if update:
            data.update(update)
        return type(self).model_validate(data)


def _serialization_key(finfo: FieldInfo, by_alias: bool) -> str:
    """Choose the output key for a field."""
    if by_alias and finfo.serialization_alias:
        return finfo.serialization_alias
    if by_alias and finfo.alias:
        return finfo.alias
    return finfo.name


def _dump_value(value: Any, *, by_alias: bool, mode: str) -> Any:
    """Recursively serialize a value."""
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=by_alias, mode=mode)
    if isinstance(value, (list, tuple)):
        return [_dump_value(item, by_alias=by_alias, mode=mode) for item in value]
    if isinstance(value, dict):
        return {
            k: _dump_value(v, by_alias=by_alias, mode=mode) for k, v in value.items()
        }
    if mode == "json" and not isinstance(value, (str, int, float, bool)):
        return str(value)
    return value


class BaseSettings(BaseModel):
    """Base per i modelli di impostazioni.

    Storicamente questa classe caricava valori dalle variabili d'ambiente
    (``env_prefix``/``env_nested_delimiter``), ma nessuna ``JENNY_*`` mappa a
    un campo di ``Config``: le env operative sono gestite in modo esplicito da
    ``jenny.config.runtime_env``. Il caricamento env è quindi rimosso e la
    classe resta come punto di estensione/compat per l'API consumata da
    ``Config``. Il modulo resta stdlib-only (vedi FORK_BOUNDARY.md).
    """

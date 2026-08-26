import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.base import instance_state

# Formatage JSON générique et unique pour toute l'API : convertit récursivement
# n'importe quelle instance mappée (avec ses relations chargées) ou valeur scalaire
# en structures JSON-safe, sans que chaque entité ait besoin de sa propre logique.


def serialize(value: Any, _seen: frozenset = frozenset()) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple, set)):
        return [serialize(item, _seen) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item, _seen) for key, item in value.items()}
    return _serialize_mapped_instance(value, _seen)


def _serialize_mapped_instance(obj: Any, _seen: frozenset) -> Any:
    mapper = sa_inspect(obj.__class__, raiseerr=False)
    if mapper is None:
        return obj

    identity = (obj.__class__, id(obj))
    if identity in _seen:
        return None
    _seen = _seen | {identity}

    state = instance_state(obj)
    hidden_fields = getattr(obj.__class__, "_hidden_fields", frozenset())
    data: dict[str, Any] = {}
    for column in mapper.columns:
        if column.key in hidden_fields:
            continue
        data[column.key] = serialize(getattr(obj, column.key), _seen)
    for relationship in mapper.relationships:
        if relationship.key in state.unloaded:
            continue
        data[relationship.key] = serialize(getattr(obj, relationship.key), _seen)
    return data


def success_response(data: Any, *, meta: dict | None = None) -> dict:
    payload: dict[str, Any] = {"success": True, "data": serialize(data)}
    if meta is not None:
        payload["meta"] = meta
    return payload


def error_response(code: str, message: str, *, details: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"success": False, "error": error}

from datetime import date, datetime
from decimal import Decimal

from app.crud.columns import python_type
from app.crud.model import EntityModel

_JSON_TYPES: dict[type, dict] = {
    int: {"type": "integer"},
    float: {"type": "number"},
    Decimal: {"type": "number"},
    bool: {"type": "boolean"},
    str: {"type": "string"},
    datetime: {"type": "string", "format": "date-time"},
    date: {"type": "string", "format": "date"},
}


def build_entity_read_schema(model: type[EntityModel]) -> dict:
    """Schéma JSON (informatif, pas de validation) des colonnes renvoyées par
    l'entité, déduit des colonnes réfléchies comme `build_default_validator` déduit
    les validateurs create/update. Utilisé par `app/mcp/` pour documenter la sortie
    des outils MCP. Les relations chargées via `with=` n'y figurent pas : leur
    présence dépend de la requête, pas de l'entité elle-même.
    """
    properties: dict[str, dict] = {}
    for column in model.table.columns:
        if column.name in model.hidden_fields:
            continue
        schema = dict(_JSON_TYPES.get(python_type(column), {"type": "string"}))
        if column.nullable:
            schema = {"anyOf": [schema, {"type": "null"}]}
        properties[column.name] = schema
    return {"type": "object", "properties": properties}

from sqlalchemy.sql.elements import ColumnElement

from app.crud.model import EntityModel
from app.exceptions import InvalidSortError

_DIRECTIONS = {"ASC", "DESC"}


def compile_order_by(source: str, model: type[EntityModel]) -> list[ColumnElement]:
    """Parse `orderby=email.ASC,created_at.DESC` en clauses SQLAlchemy `ORDER BY`
    filtrables sur `model`. Direction optionnelle (défaut `ASC`) : `orderby=email`
    fonctionne aussi.
    """
    clauses = []
    for part in source.split(","):
        part = part.strip()
        if not part:
            continue

        field, sep, direction = part.rpartition(".")
        if not sep:
            field, direction = part, "ASC"
        direction = direction.upper()

        if not field or direction not in _DIRECTIONS:
            raise InvalidSortError(
                f"Tri invalide : {part!r} (format attendu 'champ.ASC' ou 'champ.DESC')."
            )
        if field not in model.table.columns:
            raise InvalidSortError(f"Champ de tri inconnu pour '{model.name}' : {field!r}.")

        column = model.table.columns[field]
        clauses.append(column.asc() if direction == "ASC" else column.desc())

    return clauses

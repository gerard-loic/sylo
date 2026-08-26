from datetime import date, datetime
from decimal import Decimal

_SUPPORTED_TYPES = {int, float, str, bool, Decimal, datetime, date}


def python_type(column) -> type:
    """Type Python d'une colonne SQLAlchemy réfléchie. Replié sur `str` pour tout type
    non explicitement supporté (ex: UUID) : partagé par les validateurs (`validators.py`)
    et les filtres (`filters.py`) pour garder un jeu de types cohérent entre les deux.
    """
    try:
        py_type = column.type.python_type
    except NotImplementedError:
        return str
    return py_type if py_type in _SUPPORTED_TYPES else str

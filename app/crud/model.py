from typing import Any, ClassVar

from app.crud.relationships import ManyToMany, ManyToOne, OneToMany

Relationship = OneToMany | ManyToOne | ManyToMany

_REGISTRY: dict[str, type["EntityModel"]] = {}


class EntityModel:
    """Classe de base à hériter dans chaque `entities/<nom>/model.py`.

    Seuls `name`, `table_name` et éventuellement `relationships` / `url_prefix` sont à
    déclarer : les colonnes ne sont jamais écrites en dur, elles sont déduites de la
    table PostgreSQL au démarrage (voir `core/crud/mapper.py`).
    """

    name: ClassVar[str]
    table_name: ClassVar[str]
    url_prefix: ClassVar[str | None] = None
    relationships: ClassVar[list[Relationship]] = []
    # Colonnes exclues de la sérialisation JSON (ex: password) : jamais retournées par
    # l'API, quelle que soit la route. Ne les exclut pas des validateurs create/update :
    # elles restent écrivables, seule la lecture est bloquée.
    hidden_fields: ClassVar[frozenset[str]] = frozenset()

    # Renseigné dynamiquement par build_orm_mappings(), ne pas définir à la main.
    table: ClassVar[Any] = None
    python_class: ClassVar[type] = None
    mapper: ClassVar[Any] = None
    pk_column: ClassVar[Any] = None

    @classmethod
    def pk_name(cls) -> str:
        return cls.pk_column.key

    @classmethod
    def cast_pk(cls, raw: str) -> Any:
        return cls.pk_column.type.python_type(raw)

    @classmethod
    def prefix(cls) -> str:
        return cls.url_prefix or f"/{cls.name}s"


def register_model(model_cls: type[EntityModel]) -> type[EntityModel]:
    if not getattr(model_cls, "name", None) or not getattr(model_cls, "table_name", None):
        raise ValueError(f"{model_cls.__name__} doit définir 'name' et 'table_name'.")
    _REGISTRY[model_cls.name] = model_cls
    return model_cls


def get_model(name: str) -> type[EntityModel]:
    return _REGISTRY[name]


def get_all_models() -> dict[str, type[EntityModel]]:
    return _REGISTRY

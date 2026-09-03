from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Engine
from sqlalchemy.orm import registry as orm_registry
from sqlalchemy.orm import relationship

from app.crud.model import get_all_models
from app.crud.relationships import ManyToMany, ManyToOne, OneToMany

_mapper_registry = orm_registry()
_metadata = MetaData()
_association_tables: dict[str, Table] = {}
_built = False


def build_orm_mappings(engine: Engine) -> None:
    """Réflechit chaque table déclarée par les entités enregistrées et construit les
    classes ORM correspondantes, colonnes et relations comprises. C'est le seul
    endroit qui parle à PostgreSQL pour connaître la forme des entités : aucune
    colonne n'est jamais déclarée en dur côté Python.
    """
    global _built
    if _built:
        return

    models = get_all_models()

    for model in models.values():
        model.table = Table(model.table_name, _metadata, autoload_with=engine)
        model.python_class = type(
            f"{model.name.title()}Record", (), {"_hidden_fields": frozenset(model.hidden_fields)}
        )
        pk_columns = list(model.table.primary_key.columns)
        if not pk_columns:
            raise RuntimeError(f"La table '{model.table_name}' does not have a primary key.")
        model.pk_column = pk_columns[0]

        unknown_anonymized = set(model.anonymized_fields) - set(model.table.c.keys())
        if unknown_anonymized:
            raise RuntimeError(
                f"Entity '{model.table_name}' does not have column(s) "
                f"{sorted(unknown_anonymized)} configured dans anonymized_fields."
            )

    for model in models.values():
        for rel in model.relationships:
            if isinstance(rel, ManyToMany) and rel.association_table not in _association_tables:
                _association_tables[rel.association_table] = Table(
                    rel.association_table, _metadata, autoload_with=engine
                )

    for model in models.values():
        model.mapper = _mapper_registry.map_imperatively(model.python_class, model.table)

    for model in models.values():
        for rel in model.relationships:
            target_model = models[rel.target]
            prop = _build_relationship_property(model, target_model, rel)
            model.mapper.add_property(rel.attribute, prop)

    _built = True


def get_association_table(name: str) -> Table:
    return _association_tables[name]


def _build_relationship_property(model, target_model, rel):
    if isinstance(rel, OneToMany):
        fk_column = target_model.table.c[rel.foreign_key]
        return relationship(
            target_model.python_class,
            primaryjoin=model.pk_column == fk_column,
            foreign_keys=[fk_column],
            uselist=True,
            viewonly=True,
        )
    if isinstance(rel, ManyToOne):
        fk_column = model.table.c[rel.foreign_key]
        return relationship(
            target_model.python_class,
            primaryjoin=fk_column == target_model.pk_column,
            foreign_keys=[fk_column],
            uselist=False,
            viewonly=True,
        )
    if isinstance(rel, ManyToMany):
        association = _association_tables[rel.association_table]
        return relationship(
            target_model.python_class,
            secondary=association,
            primaryjoin=model.pk_column == association.c[rel.local_key],
            secondaryjoin=target_model.pk_column == association.c[rel.remote_key],
            uselist=True,
            viewonly=True,
        )
    raise TypeError(f"Relation type non supported : {rel!r}")

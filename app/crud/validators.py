from typing import Any

from pydantic import ConfigDict, create_model

from app.crud.columns import python_type
from app.crud.model import EntityModel, get_model
from app.crud.relationships import ManyToMany


def build_default_validator(model: type[EntityModel], *, mode: str):
    """Déduit un validateur Pydantic à partir des colonnes réfléchies de la table,
    utilisé quand l'entité ne fournit pas son propre validators/post.py ou put.py.
    La clé primaire est toujours exclue (elle vient de l'URL, pas du corps de la
    requête). En mode 'create', une colonne NOT NULL sans valeur par défaut est requise ;
    en mode 'update' tous les champs sont optionnels (mise à jour partielle).

    Chaque relation N:N donne aussi un champ optionnel `<attribut>: list[<pk cible>]`
    (ex: `roles: list[int]`) : la liste des identifiants de l'entité cible à associer,
    synchronisée par `BaseCRUDMethods` (remplace l'association existante). Absent du
    payload -> inchangé ; `[]` -> vide toute l'association.
    """
    fields: dict[str, tuple[Any, Any]] = {}
    for column in model.table.columns:
        if column is model.pk_column:
            continue

        py_type = python_type(column)
        if mode == "update":
            fields[column.name] = (py_type | None, None)
            continue

        required = not column.nullable and column.default is None and column.server_default is None
        fields[column.name] = (py_type, ...) if required else (py_type | None, None)

    for rel in model.relationships:
        if isinstance(rel, ManyToMany):
            target_pk_type = python_type(get_model(rel.target).pk_column)
            fields[rel.attribute] = (list[target_pk_type] | None, None)

    suffix = "Create" if mode == "create" else "Update"
    label = f"{model.name.title()}{suffix}Validator"
    return create_model(label, __config__=ConfigDict(extra="forbid"), **fields)

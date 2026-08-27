import importlib
import pkgutil
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from app.crud.mapper import build_orm_mappings
from app.crud.model import get_all_models
from app.crud.validators import build_default_validator

# Les entités "coeur" (CRUD permission/role/sendmail/user/usertoken) vivent dans
# app/entities/. Pour surcharger intégralement l'une d'elles (model.py, methods.py,
# routes.py, validators/...), il suffit de créer un dossier du même nom ici, dans
# entities/ : ce dossier est alors utilisé à la place de son équivalent dans
# app/entities/. C'est aussi ici que vivent les entités propres au projet, qui
# n'ont pas d'équivalent dans app/entities/.
_CORE_PACKAGE = "app.entities"
_OVERRIDE_PACKAGE = "entities"


def _discover_package_names(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {module.name for module in pkgutil.iter_modules([str(directory)]) if module.ispkg}


def _resolve_validator(package: str, entity_name: str, mode: str, model):
    """mode: 'post' ou 'put'. Cherche un validateur défini par l'entité (validators/post.py
    ou validators/put.py, classes CreateValidator / UpdateValidator) ; à défaut, un
    validateur Pydantic est déduit automatiquement des colonnes de la table.
    """
    attr_name = "CreateValidator" if mode == "post" else "UpdateValidator"
    build_mode = "create" if mode == "post" else "update"
    try:
        module = importlib.import_module(f"{package}.{entity_name}.validators.{mode}")
    except ModuleNotFoundError:
        return build_default_validator(model, mode=build_mode)
    validator = getattr(module, attr_name, None)
    if validator is None:
        return build_default_validator(model, mode=build_mode)
    return validator


def register_entities(app: FastAPI, engine: Engine) -> None:
    override_dir = Path(__file__).resolve().parent
    core_dir = override_dir.parent / "app" / "entities"

    override_names = _discover_package_names(override_dir)
    core_names = _discover_package_names(core_dir)
    entity_names = sorted(core_names | override_names)
    packages = {
        name: _OVERRIDE_PACKAGE if name in override_names else _CORE_PACKAGE
        for name in entity_names
    }

    for entity_name in entity_names:
        importlib.import_module(f"{packages[entity_name]}.{entity_name}.model")

    build_orm_mappings(engine)
    models = get_all_models()

    for entity_name in entity_names:
        package = packages[entity_name]
        model = models[entity_name]

        methods_module = importlib.import_module(f"{package}.{entity_name}.methods")
        methods_cls = getattr(methods_module, f"{entity_name.title()}Methods")
        methods = methods_cls(model)

        create_validator = _resolve_validator(package, entity_name, "post", model)
        update_validator = _resolve_validator(package, entity_name, "put", model)

        routes_module = importlib.import_module(f"{package}.{entity_name}.routes")
        router = routes_module.build_router(
            model,
            methods,
            create_validator=create_validator,
            update_validator=update_validator,
        )
        app.include_router(router)

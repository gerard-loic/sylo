from app.crud.routes import build_crud_router


def build_router(model, methods, *, create_validator, update_validator):
    # Table de session : alimentée uniquement par UserMethods.login, aucune route
    # HTTP n'est exposée dessus.
    return build_crud_router(
        model,
        methods,
        create_validator=create_validator,
        update_validator=update_validator,
        enabled=(),
    )

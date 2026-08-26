from app.crud.routes import build_crud_router


def build_router(model, methods, *, create_validator, update_validator):
    return build_crud_router(
        model,
        methods,
        create_validator=create_validator,
        update_validator=update_validator,
        enabled=("list", "get", "create", "update", "delete"),
    )

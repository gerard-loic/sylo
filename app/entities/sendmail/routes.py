from app.crud.routes import build_crud_router


def build_router(model, methods, *, create_validator, update_validator):
    # Table de log interne : alimentée uniquement par le code (ex: UserMethods.create
    # après l'envoi d'un email), aucune route HTTP n'est exposée dessus.
    return build_crud_router(
        model,
        methods,
        create_validator=create_validator,
        update_validator=update_validator,
        enabled=(),
    )

from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth import authenticate, mark_public
from app.database import get_db
from app.exceptions import UnauthorizedError
from app.responses import success_response
from app.crud.routes import build_crud_router


class LoginValidator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


def _parse_with(with_: str | None) -> list[str]:
    if not with_:
        return []
    return [item.strip() for item in with_.split(",") if item.strip()]


def build_router(model, methods, *, create_validator, update_validator):
    router = build_crud_router(
        model,
        methods,
        create_validator=create_validator,
        update_validator=update_validator,
        enabled=("list", "get", "create", "delete"),  # "update" : route custom ci-dessous
    )

    @router.post("/login")
    def login(payload: LoginValidator, db: Session = Depends(get_db)):
        result = methods.login(db, payload.email, payload.password)
        return success_response(result)

    mark_public(f"{model.prefix()}/login", "POST")


    @router.put("/{item_id}")
    def update_item(
        item_id: str,
        payload: update_validator,  # type: ignore[valid-type]
        request: Request,
        with_: str | None = Query(
            None, alias="with", description="Relations à charger, séparées par des virgules"
        ),
        db: Session = Depends(get_db),
    ):
        data = payload.model_dump(exclude_unset=True)

        try:
            user_id = model.cast_pk(item_id)
        except (TypeError, ValueError):
            user_id = None

        if user_id is not None:
            # PUT /users/{id} : mise à jour standard, Bearer + permission requis.
            authenticate(request, db)
            obj = methods.update(db, user_id, data, with_=_parse_with(with_))
            return success_response(obj)

        # PUT /users/{initial_token} : pas de Bearer requis, mais le token doit
        # correspondre à un utilisateur et `password` doit être fourni. En cas de
        # succès, `initial_token` est consommé (remis à NULL).
        if not data.get("password"):
            raise UnauthorizedError("Password required")
        user = methods.get_by_initial_token(db, item_id)
        if user is None:
            raise UnauthorizedError("Invalid token")
        obj = methods.update(
            db,
            getattr(user, model.pk_name()),
            {**data, "initial_token": None},
            with_=_parse_with(with_),
        )
        return success_response(obj)

    mark_public(f"{model.prefix()}/{{item_id}}", "PUT")

    return router

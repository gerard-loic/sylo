from typing import Iterable, Type

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.responses import success_response
from app.crud.filters import compile_filter
from app.crud.methods import BaseCRUDMethods
from app.crud.model import EntityModel
from app.crud.ordering import compile_order_by

_settings = get_settings()

CRUD_OPERATIONS = ("list", "get", "create", "update", "delete")

_ORDERBY_DESCRIPTION = (
    "Tri, ex: email.ASC ou email.DESC (plusieurs champs séparés par des virgules)."
)

_FILTER_DESCRIPTION = (
    "Expression de filtre sur les colonnes de l'entité, ex: \"status == 'active' AND "
    "(age >= 18 OR role IN ('admin', 'owner') OR name LIKE 'ali')\". Opérateurs : == "
    "!= < > <= >= IN, NOT IN, LIKE (recherche insensible à la casse, 'contient', "
    "uniquement sur des champs texte), SOUNDEX (recherche phonétique via l'extension "
    "PostgreSQL fuzzystrmatch, ex: name SOUNDEX 'Alisse', champs texte uniquement), "
    "SIMILAR (recherche lexicale tolérante aux fautes via l'extension PostgreSQL "
    "pg_trgm, ex: name SIMILAR 'Alisse', champs texte uniquement). "
    "AND est prioritaire sur OR, parenthèses "
    "supportées. Valeurs : nombre, 'texte' entre quotes, true/false, null, ou liste "
    "entre parenthèses pour IN/NOT IN, ex: role IN ('admin', 'owner')."
)


def _parse_with(with_: str | None) -> list[str]:
    if not with_:
        return []
    return [item.strip() for item in with_.split(",") if item.strip()]


def _resolve_offset(page: int | None, offset: int, limit: int) -> int:
    """`page` (1-indexée) prime sur `offset` quand elle est fournie."""
    if page is not None:
        return (page - 1) * limit
    return offset


class _QueryPayload(BaseModel):
    """Corps de la méthode HTTP `QUERY` : équivalent de `list`, mais où le filtre (trop
    riche pour une query string) voyage dans le corps de la requête plutôt que dans
    l'URL. `QUERY` est une méthode sûre et idempotente au même titre que `GET`."""

    filter: str | None = Field(default=None, description=_FILTER_DESCRIPTION)
    limit: int = Field(default=_settings.default_page_size, ge=1, le=_settings.max_page_size)
    offset: int = Field(default=0, ge=0)
    page: int | None = Field(default=None, ge=1)
    orderby: str | None = Field(default=None, description=_ORDERBY_DESCRIPTION)
    with_: list[str] | None = Field(default=None, alias="with")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def build_crud_router(
    model: type[EntityModel],
    methods: BaseCRUDMethods,
    *,
    create_validator: Type[BaseModel],
    update_validator: Type[BaseModel],
    enabled: Iterable[str] | None = None,
) -> APIRouter:
    """Construit les routes REST standard (list/get/create/update/delete) pour une
    entité. Appelé depuis `entities/<nom>/routes.py`, qui peut ensuite ajouter ou
    surcharger des routes sur le router retourné.

    `enabled` restreint les opérations exposées, ex: `enabled=("list", "get")` pour
    une entité en lecture seule. Par défaut (`None`), les 5 opérations sont exposées.
    """
    enabled_ops = set(CRUD_OPERATIONS) if enabled is None else set(enabled)
    unknown_ops = enabled_ops - set(CRUD_OPERATIONS)
    if unknown_ops:
        raise ValueError(
            f"Opération(s) inconnue(s) pour '{model.name}': {sorted(unknown_ops)} "
            f"(valides: {CRUD_OPERATIONS})"
        )

    router = APIRouter(prefix=model.prefix(), tags=[model.name])

    if "list" in enabled_ops:

        @router.get("")
        def list_items(
            limit: int = Query(_settings.default_page_size, ge=1, le=_settings.max_page_size),
            offset: int = Query(0, ge=0),
            page: int | None = Query(None, ge=1, description="Page affichée (1-indexée, prime sur offset)"),
            orderby: str | None = Query(None, description=_ORDERBY_DESCRIPTION),
            with_: str | None = Query(
                None, alias="with", description="Relations à charger, séparées par des virgules"
            ),
            db: Session = Depends(get_db),
        ):
            resolved_offset = _resolve_offset(page, offset, limit)
            order_clauses = compile_order_by(orderby, model) if orderby else None
            items, total = methods.list(
                db,
                limit=limit,
                offset=resolved_offset,
                with_=_parse_with(with_),
                order_by=order_clauses,
            )
            return success_response(
                items,
                meta={
                    "total": total,
                    "limit": limit,
                    "offset": resolved_offset,
                    "page": resolved_offset // limit + 1,
                },
            )

        def _run_query(payload: _QueryPayload, db: Session):
            filter_clause = compile_filter(payload.filter, model) if payload.filter else None
            order_clauses = compile_order_by(payload.orderby, model) if payload.orderby else None
            resolved_offset = _resolve_offset(payload.page, payload.offset, payload.limit)
            items, total = methods.list(
                db,
                limit=payload.limit,
                offset=resolved_offset,
                with_=payload.with_ or [],
                filter_clause=filter_clause,
                order_by=order_clauses,
            )
            return success_response(
                items,
                meta={
                    "total": total,
                    "limit": payload.limit,
                    "offset": resolved_offset,
                    "page": resolved_offset // payload.limit + 1,
                },
            )

        @router.api_route("", methods=["QUERY"])
        def query_items(payload: _QueryPayload, db: Session = Depends(get_db)):
            return _run_query(payload, db)

        @router.post("/query")
        def query_items_fallback(payload: _QueryPayload, db: Session = Depends(get_db)):
            # Repli pour les clients/proxys qui ne relaient pas encore la méthode HTTP
            # QUERY (brouillon IETF) : même payload, même logique de filtrage.
            return _run_query(payload, db)

    if "get" in enabled_ops:

        @router.get("/{item_id}")
        def get_item(
            item_id: str,
            with_: str | None = Query(
                None, alias="with", description="Relations à charger, séparées par des virgules"
            ),
            db: Session = Depends(get_db),
        ):
            obj = methods.get_or_404(db, model.cast_pk(item_id), with_=_parse_with(with_))
            return success_response(obj)

    if "create" in enabled_ops:

        @router.post("", status_code=201)
        def create_item(
            payload: create_validator,  # type: ignore[valid-type]
            with_: str | None = Query(
                None, alias="with", description="Relations à charger, séparées par des virgules"
            ),
            db: Session = Depends(get_db),
        ):
            obj = methods.create(db, payload.model_dump(exclude_unset=True), with_=_parse_with(with_))
            return success_response(obj)

    if "update" in enabled_ops:

        @router.put("/{item_id}")
        def update_item(
            item_id: str,
            payload: update_validator,  # type: ignore[valid-type]
            with_: str | None = Query(
                None, alias="with", description="Relations à charger, séparées par des virgules"
            ),
            db: Session = Depends(get_db),
        ):
            obj = methods.update(
                db, model.cast_pk(item_id), payload.model_dump(exclude_unset=True), with_=_parse_with(with_)
            )
            return success_response(obj)

    if "delete" in enabled_ops:

        @router.delete("/{item_id}", status_code=200)
        def delete_item(item_id: str, db: Session = Depends(get_db)):
            methods.delete(db, model.cast_pk(item_id))
            return success_response(None)

    return router

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import enforce_auth, mark_public
from app.database import engine
from app.exceptions import ApiError
from app.logs import configure_error_logger
from app.responses import error_response
from entities import register_entities

logger = configure_error_logger(logging.getLogger("wakaru_api"))

# `enforce_auth` (app/auth.py) s'applique à toutes les routes de l'app : Bearer token
# + permission requis par défaut. Les exceptions (ex: /users/login) sont déclarées
# dans le code via `mark_public()`, voir app/entities/user/routes.py.
app = FastAPI(title="Wakaru API", dependencies=[Depends(enforce_auth)])

mark_public("/health", "GET")


@app.exception_handler(ApiError)
def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error(
            "Erreur %s sur %s %s : %s", exc.status_code, request.method, request.url.path, exc.message
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(exc.code, exc.message, details=exc.details),
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_response("validation_error", "Données invalides.", details=exc.errors()),
    )


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Erreur non gérée sur %s %s : %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content=error_response("internal_error", "Erreur interne du serveur."),
    )


@app.get("/health")
def health_check() -> dict:
    return {"success": True, "data": {"status": "ok"}}


register_entities(app, engine)

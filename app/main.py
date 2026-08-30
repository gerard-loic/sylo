import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import enforce_auth, mark_public
from app.config import get_settings
from app.database import engine
from app.exceptions import ApiError
from app.logs import configure_error_logger
from app.responses import error_response
from entities import register_entities

logger = configure_error_logger(logging.getLogger("sylo_api"))
_settings = get_settings()

# Renseigné plus bas si SYLO_MCP_ENABLED (voir la fin du fichier) : `lifespan` y
# fait référence par nom, résolu seulement au démarrage réel du serveur, une fois le
# module entièrement chargé.
_mcp_manager = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _mcp_manager is None:
        yield
        return
    async with _mcp_manager.run():
        yield


# `enforce_auth` (app/auth.py) s'applique à toutes les routes de l'app : Bearer token
# + permission requis par défaut. Les exceptions (ex: /users/login) sont déclarées
# dans le code via `mark_public()`, voir app/entities/user/routes.py.
app = FastAPI(title="Sylo API", dependencies=[Depends(enforce_auth)], lifespan=lifespan)

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

if _settings.mcp_enabled:
    # Doit être construit après register_entities() : le catalogue d'outils MCP est
    # dérivé des routes réellement enregistrées (voir app/mcp/catalog.py).
    from app.mcp.server import build_session_manager

    _mcp_manager = build_session_manager(app)
    app.mount("/mcp", _mcp_manager.handle_request)

# custom/ : code propre au projet

Tout le code spécifique à un projet issu de cette base va ici, **jamais dans `app/`**.
Si un besoin oblige à modifier `app/`, il manque un point d'extension dans la base.

Seul ce squelette est versionné dans le dépôt de base (voir `.gitignore`). En Docker,
le dossier est monté sur `/app/custom` (voir `docker/docker-compose.yml`).

```
custom/
  __init__.py         # optionnel : hook setup(app), voir plus bas
  entities/           # entités du projet + surcharges des entités du core
  mails/              # gabarits d'emails (surcharge / ajout)
  config/             # fichiers de config des scripts (generate-config.json...)
  scripts/            # commandes ./cmd.sh propres au projet
  libs/               # classes et modules Python additionnels
  requirements.txt    # optionnel : dépendances pip additionnelles
```

## entities/

Une entité `custom/entities/<nom>/` (`model.py`, `methods.py`, `routes.py`,
`validators/` optionnel) est chargée au démarrage (`app/extensions.py`). Si
`app/entities/<nom>/` existe, la version de `custom/` la remplace intégralement.
`./cmd.sh generate_entities` écrit ici.

`model.py` est optionnel : pour des routes purement spécifiques, sans table
derrière, seul `routes.py` est requis (avec `__init__.py`). Sa fonction
`build_router()` est alors appelée **sans argument** (pas de CRUD généré, pas de
`methods.py` ni de validateurs chargés) :

```python
# custom/entities/stats/routes.py
from fastapi import APIRouter

from app.responses import success_response


def build_router() -> APIRouter:
    router = APIRouter(prefix="/stats", tags=["stats"])

    @router.get("/summary")
    def summary():
        return success_response({"ok": True})

    return router
```

Ces routes sont soumises à l'authentification, prises en compte par
`sync_permissions` et exposées en MCP comme les autres.

## mails/

Un fichier de même chemin relatif que dans `app/mail/templates/` y est prioritaire
(ex : `custom/mails/welcome/html.j2`). On peut aussi y ajouter de nouveaux gabarits
et des assets (`custom/mails/assets/`, `custom/mails/<gabarit>/assets/`).

## config/

Fichiers passés aux scripts, ex :
`./cmd.sh sync_permissions --config-file custom/config/generate-config.json`.

## scripts/

`custom/scripts/<commande>.py` se lance avec `./cmd.sh <commande>` (ou
`make cmd c="<commande>"`). À nom égal, il remplace le script du core. En tête de
script, rendre la racine du projet importable :

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

## libs/

Modules Python libres, importés par leur chemin complet :
`from custom.libs.mon_module import MaClasse`.

## \_\_init\_\_.py : hook setup(app)

Pour tout ce qui n'est pas une entité (router transverse, middleware, handler
d'exception, route publique...), définir `setup` dans `custom/__init__.py`. Il est
appelé après l'enregistrement des entités et avant la construction du serveur MCP,
donc les routes ajoutées ici sont soumises à l'authentification et exposées en MCP
comme les autres.

```python
from fastapi import FastAPI


def setup(app: FastAPI) -> None:
    from custom.libs.stats import router as stats_router

    app.include_router(stats_router)
```

## requirements.txt

Installé au build de l'image (`docker/Dockerfile`) : relancer `make up` après
modification. En local : `pip install -r custom/requirements.txt`.

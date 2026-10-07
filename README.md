# Sylo

Sylo est une base d'API REST en Python (FastAPI + SQLAlchemy + PostgreSQL) qui
**génère les CRUD à partir de la base de données**. Les colonnes ne sont jamais
déclarées en Python : chaque entité indique seulement sa table et ses relations, le
reste est lu dans PostgreSQL au démarrage.

Fonctionnalités incluses :

- **CRUD générique** pour chaque entité : `list`, `get`, `create`, `update`, `delete`,
  avec pagination, tri, chargement des relations à la demande et filtres riches
  (`==`, `IN`, `LIKE`, `SOUNDEX`, `SIMILAR`, `AND`/`OR`, parenthèses).
- **Relations** 1:N, N:1 et N:N, déclarées en une ligne.
- **Validation** déduite des colonnes (NOT NULL, types), surchargeable par entité.
- **Suppression logique** (`deleted_at`), avec anonymisation possible de certains champs.
- **Authentification** par Bearer token et **permissions** par route/méthode,
  attribuées à des rôles.
- **Emails** à partir de gabarits Jinja2 (bienvenue, choix du mot de passe...),
  journalisés en base.
- **Documentation** OpenAPI automatique (`/docs`, `/redoc`, `/openapi.json`).
- **Serveur MCP** optionnel : chaque route de l'API devient un outil utilisable par
  un assistant IA, filtré selon les permissions de l'utilisateur.
- **Scripts** pour générer les entités, synchroniser les permissions, créer rôles et
  utilisateurs, exporter une collection Bruno.

### Le cœur et le code du projet

Sylo est distribué comme une image Docker (`ghcr.io/gerard-loic/sylo`). Un projet
n'en modifie jamais le code (`app/`, `scripts/`) : tout ce qui lui est propre vit
dans le dossier `custom/`, monté dans le conteneur. Mettre à jour Sylo revient à
changer de version d'image.

```
app/            cœur : CRUD générique, auth, mails, MCP (ne pas modifier dans un projet)
  entities/     entités du cœur : user, role, permission, usertoken, sendmail
scripts/        commandes du cœur, lancées via ./cmd.sh
custom/         code propre au projet (voir « Écrire du code spécifique »)
```

---

## Installation

Un nouveau projet démarre depuis le dépôt
[sylo-starter](https://github.com/gerard-loic/sylo-starter). Il contient le dossier
`custom/` vide, la configuration Docker (image Sylo + reverse-proxy Traefik) et un
`Makefile`.

### Prérequis

- Docker et Docker Compose
- `make`
- Une base PostgreSQL accessible depuis le conteneur

### 1. Récupérer le starter

```bash
git clone https://github.com/gerard-loic/sylo-starter mon-projet
cd mon-projet
rm -rf .git && git init   # repartir d'un historique propre pour le projet
```

### 2. Configurer

```bash
cp .env.exemple .env                 # configuration de l'application (voir plus bas)
cp docker/.env.exemple docker/.env   # configuration Docker
```

Dans `docker/.env` :

| Variable | Rôle |
|---|---|
| `COMPOSE_PROJECT_NAME` | Nom du conteneur et du routeur Traefik |
| `COMPOSE_PROJECT_HOST` | Nom d'hôte seul, sans `http://` ni slash (ex : `mon-api.local`, `api.mondomaine.fr`) |
| `COMPOSE_PROJECT_PORT` | Port interne de l'application (`8002`) |
| `COMPOSE_SYLO_VERSION` | Version de l'image Sylo à utiliser (tag git du cœur, ex : `1.0.0`) |
| `COMPOSE_RESTART_POLICY` | `no` en local, `unless-stopped` en production |
| `COMPOSE_TRAEFIK_ENTRYPOINT` | `http` en local, `https` en production (Let's Encrypt) |

### 3. Préparer la base PostgreSQL

Activer les extensions utilisées par les filtres `SOUNDEX` et `SIMILAR` :

```sql
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;  -- SOUNDEX
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- SIMILAR
```

Les entités du cœur s'appuient sur les tables suivantes. Toutes les tables d'entité
ont une clé primaire simple et une colonne `deleted_at` (suppression logique).

| Table | Colonnes utilisées par le cœur |
|---|---|
| `users` | `id`, `email`, `first_name`, `last_name`, `password` (nullable), `initial_token` (nullable), `deleted_at` |
| `roles` | `id`, `name`, `uid`, `deleted_at` |
| `permissions` | `id`, `uid`, `route`, `method`, `deleted_at` |
| `user_tokens` | `id`, `user_id`, `token`, `expires_at`, `deleted_at` |
| `sendmails` | `id`, `from`, `to`, `subject`, `content`, `deleted_at` |
| `user_role` | `user_id`, `role_id` (table d'association) |
| `role_permission` | `role_id`, `permission_id` (table d'association) |

Les tables propres au projet suivent les mêmes règles : clé primaire simple, colonne
`deleted_at`, et, pour une relation N:N, une table d'association à exactement deux
colonnes, chacune clé étrangère.

### 4. Démarrer

```bash
make traefik-up   # reverse-proxy local (HTTP, tableau de bord sur :8080)
make up           # télécharge l'image Sylo et démarre l'application
```

En local, ajouter le nom d'hôte dans `/etc/hosts` :

```
127.0.0.1 mon-api.local
```

L'API répond alors sur `http://mon-api.local` :

- `GET /health` : état du service
- `/docs` (Swagger), `/redoc`, `/openapi.json` : documentation

Pour la production (HTTPS), voir `docker/traefik/README.md` du starter :
`make traefik-prod-up` puis `make up`.

### 5. Initialiser les droits et le premier utilisateur

```bash
# Générer les entités du projet à partir des tables
make cmd c="generate_entities --config-file custom/config/generate-config.json"
make down && make up   # redémarrer pour charger les nouvelles entités

# Créer une permission par route
make cmd c="sync_permissions --config-file custom/config/generate-config.json"

# Créer le rôle administrateur avec ses permissions
make cmd c="create_role --name Administrateur --uid ADMIN --config-file custom/config/generate-config.json"

# Créer le premier utilisateur
make cmd c="create_user --email admin@exemple.fr --first_name Admin --last_name Sylo --password secret --role-uid ADMIN"
```

On obtient ensuite un token avec `POST /users/login` (`{"email": "...", "password": "..."}`),
à envoyer dans l'en-tête `Authorization: Bearer <token>` de chaque requête.

### Travailler sur le cœur (sans Docker)

Pour développer Sylo lui-même, dans ce dépôt :

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

---

## Le fichier de configuration `.env`

L'application lit sa configuration dans le fichier `.env` à la racine (et dans les
variables d'environnement, qui sont prioritaires). Toutes les variables sont
préfixées par `SYLO_` ; celles qui sont absentes prennent la valeur par défaut
définie dans `app/config.py`.

```dotenv
# Base de données (format SQLAlchemy). Depuis le conteneur, une base sur la machine
# hôte est joignable via host.docker.internal.
SYLO_DATABASE_URL=postgresql+psycopg2://sylo:sylo@host.docker.internal:5432/sylo

# Pagination : taille de page par défaut et maximum accepté pour `limit`
SYLO_DEFAULT_PAGE_SIZE=50
SYLO_MAX_PAGE_SIZE=200

# CORS : origines autorisées, au format JSON. Par défaut ["*"].
SYLO_CORS_ALLOW_ORIGINS='["http://localhost:3000","https://mon-front.fr"]'

# Envoi d'emails (SMTP)
SYLO_SMTP_HOST=smtp.exemple.fr
SYLO_SMTP_PORT=587
SYLO_SMTP_USER=
SYLO_SMTP_PASSWORD=
SYLO_SMTP_USE_TLS=true      # STARTTLS
SYLO_SMTP_USE_SSL=false     # SSL direct (port 465) ; ne pas activer avec TLS
SYLO_MAIL_FROM_ADDRESS=no-reply@exemple.fr
SYLO_MAIL_FROM_NAME=Mon projet

# Sécurité
SYLO_PASSWORD_HASH_ROUNDS=12        # coût bcrypt (plus haut = plus lent et plus sûr)
SYLO_AUTH_TOKEN_TTL_MINUTES=1440    # durée de validité d'un token de connexion

# Lien envoyé par email quand un utilisateur est créé sans mot de passe :
# le token d'initialisation est ajouté à la fin de ce préfixe.
SYLO_PASSWORD_SETUP_URL_PREFIX=https://mon-front.fr/choix-mot-de-passe?token=

# Serveur MCP sur /mcp (désactivé par défaut)
SYLO_MCP_ENABLED=false
# Fichier optionnel listant les routes à ne pas exposer en MCP
# (une entrée par ligne : "users" ou "users/login:POST", '#' pour commenter)
SYLO_MCP_EXCLUDE_FILE=custom/config/mcp-exclude.txt
```

| Variable | Défaut | Description |
|---|---|---|
| `SYLO_DATABASE_URL` | `postgresql+psycopg2://sylo:sylo@localhost:5432/sylo` | Connexion PostgreSQL |
| `SYLO_DEFAULT_PAGE_SIZE` | `50` | `limit` par défaut des listes |
| `SYLO_MAX_PAGE_SIZE` | `200` | `limit` maximum accepté |
| `SYLO_CORS_ALLOW_ORIGINS` | `["*"]` | Origines CORS autorisées (liste JSON) |
| `SYLO_SMTP_HOST` / `_PORT` | `localhost` / `587` | Serveur SMTP |
| `SYLO_SMTP_USER` / `_PASSWORD` | vide | Identifiants SMTP (optionnels) |
| `SYLO_SMTP_USE_TLS` / `_USE_SSL` | `true` / `false` | Chiffrement SMTP |
| `SYLO_MAIL_FROM_ADDRESS` / `_NAME` | `no-reply@sylo.local` / `Sylo` | Expéditeur des emails |
| `SYLO_PASSWORD_HASH_ROUNDS` | `12` | Coût du hachage bcrypt |
| `SYLO_AUTH_TOKEN_TTL_MINUTES` | `1440` | Durée de vie d'un token (24 h) |
| `SYLO_PASSWORD_SETUP_URL_PREFIX` | `http://monsite.fr/choixmotdepasse?token=` | Lien de choix du mot de passe |
| `SYLO_MCP_ENABLED` | `false` | Active le serveur MCP sur `/mcp` |
| `SYLO_MCP_EXCLUDE_FILE` | vide | Routes exclues du serveur MCP |

Le fichier `.env` contient des secrets : il n'est jamais versionné. Seul
`.env.exemple` l'est. Après une modification, redémarrer le conteneur
(`make down && make up`).

---

## Les commandes (scripts)

Toutes les commandes passent par `./cmd.sh <commande> [arguments]`. Dans un projet
Docker, on les lance dans le conteneur :

```bash
make cmd c="<commande> [arguments]"
# ex : make cmd c="sync_permissions --dry-run"
```

`./cmd.sh` sans argument liste les commandes disponibles. Une commande de
`custom/scripts/` du même nom remplace celle du cœur. La plupart des commandes
acceptent `--dry-run`, qui affiche ce qui serait fait sans rien écrire.

### `generate_entities` : générer les entités depuis la base

Parcourt les tables de la base et crée une entité CRUD dans `custom/entities/<nom>/`
pour chaque table qui n'en a pas encore. Les relations sont déduites des clés
étrangères. Une table de deux colonnes qui sont toutes deux des clés étrangères est
traitée comme une table d'association N:N. Les entités du cœur (`app/entities/`)
ne sont jamais écrasées.

```bash
./cmd.sh generate_entities --dry-run
./cmd.sh generate_entities --config-file custom/config/generate-config.json
./cmd.sh generate_entities --force   # régénère les entités déjà présentes dans custom/
```

| Option | Description |
|---|---|
| `--config-file FILE` | Les tables de `exclude.routes` ne sont pas générées |
| `--force` | Écrase `model.py`, `methods.py` et `routes.py` des entités existantes dans `custom/entities/` |
| `--dry-run` | N'écrit aucun fichier |

Redémarrer l'application ensuite pour charger les nouvelles entités.

### `sync_permissions` : créer les permissions des routes

Crée dans `permissions` une ligne par route et méthode qui n'en a pas encore. L'uid
d'une permission est `<ROUTE>_<METHODE>` en majuscules, par exemple
`/USERS/{ITEM_ID}_DELETE`. Les permissions existantes ne sont jamais modifiées.

```bash
./cmd.sh sync_permissions --dry-run
./cmd.sh sync_permissions --config-file custom/config/generate-config.json
./cmd.sh sync_permissions --exclude-route /health --exclude "/permissions/{item_id}:DELETE"
```

| Option | Description |
|---|---|
| `--config-file FILE` | `exclude.permissions` : routes à ignorer ; `additionnal.permissions` : permissions libres à créer en plus |
| `--exclude-route ROUTE` | Ignore une route entière (répétable, ou séparées par des virgules) |
| `--exclude ROUTE:METHODE` | Ignore une méthode précise d'une route (répétable) |
| `--dry-run` | N'écrit rien en base |

À relancer après chaque ajout d'entité ou de route.

### `create_role` : créer un rôle

```bash
./cmd.sh create_role --name "Administrateur" --uid ADMIN
./cmd.sh create_role --name "Administrateur" --uid ADMIN --config-file custom/config/generate-config.json
```

| Option | Description |
|---|---|
| `--name` | Nom du rôle (obligatoire) |
| `--uid` | Identifiant unique, ex : `ADMIN` (obligatoire) |
| `--config-file FILE` | Associe au rôle les permissions listées dans `roles.<uid>` |
| `--dry-run` | N'écrit rien en base |

### `assign_permissions` : associer des permissions à un rôle existant

Sans configuration, associe au rôle **toutes** les permissions existantes. Les
associations déjà présentes ne sont pas touchées.

```bash
./cmd.sh assign_permissions --role-uid ADMIN
./cmd.sh assign_permissions --role-uid EDITOR --config-file custom/config/generate-config.json
./cmd.sh assign_permissions --role-uid EDITOR --exclude-file custom/config/except.txt
```

| Option | Description |
|---|---|
| `--role-uid UID` | Rôle cible (obligatoire) |
| `--config-file FILE` | Limite aux permissions de `roles.<uid>` |
| `--exclude-file FILE` | Retire de la sélection les routes listées (une par ligne : `users` ou `users/login:POST`) |
| `--dry-run` | N'écrit rien en base |

### `create_user` : créer un utilisateur

Même comportement que `POST /users` : avec `--password`, le mot de passe est haché et
un email de bienvenue est envoyé. Sans `--password`, l'utilisateur reçoit un email
avec un lien pour choisir son mot de passe.

```bash
./cmd.sh create_user --email a@b.fr --first_name Alice --last_name Martin --password secret --role-uid ADMIN
```

| Option | Description |
|---|---|
| `--email`, `--first_name`, `--last_name` | Obligatoires |
| `--password` | Mot de passe en clair (optionnel) |
| `--role-uid UID` | Rôle à associer (optionnel) |
| `--dry-run` | N'écrit rien et n'envoie aucun email |

> La commande renseigne aussi `users.level_id` avec le niveau marqué `is_default`
> dans la table `levels` (entité `level`). Un projet sans cette table doit fournir
> sa propre version dans `custom/scripts/create_user.py`.

### `generate_bruno_collection` : exporter une collection Bruno

Produit un zip importable dans [Bruno](https://www.usebruno.com/), avec une requête
par route. La requête `POST /users/login` enregistre le token reçu dans la variable
`token`, utilisée ensuite par toutes les autres requêtes.

```bash
./cmd.sh generate_bruno_collection
./cmd.sh generate_bruno_collection --output bruno/api.zip --base-url http://mon-api.local
```

| Option | Défaut |
|---|---|
| `--output FILE` | `sylo_api_bruno_collection.zip` |
| `--base-url URL` | `http://localhost:8000` |
| `--collection-name NAME` | `Sylo API` |

### Le fichier `custom/config/generate-config.json`

Un seul fichier JSON sert à toutes les commandes ; chacune lit les clés qui la
concernent.

```json
{
    "exclude": {
        "routes": ["users", "roles", "permissions", "sendmails", "user_tokens"],
        "permissions": ["users/login:POST"]
    },
    "additionnal": {
        "permissions": ["KNOWLEDGE_UNLIMITED"]
    },
    "roles": {
        "ADMIN": ["*"],
        "EDITOR": ["articles:*", "users/{item_id}:GET", "KNOWLEDGE_UNLIMITED"]
    }
}
```

| Clé | Utilisée par | Contenu |
|---|---|---|
| `exclude.routes` | `generate_entities` | Tables à ne pas générer (nom de table ou d'entité) |
| `exclude.permissions` | `sync_permissions` | Routes (`users`) ou routes/méthodes (`users/login:POST`) sans permission |
| `additionnal.permissions` | `sync_permissions` | Permissions libres, sans route, à créer |
| `roles.<UID>` | `create_role`, `assign_permissions` | Permissions du rôle : `*` (toutes), `route:METHODE`, `route:*` (toutes les méthodes de la route) ou uid d'une permission libre |

---

## Écrire un CRUD

Une entité est un dossier `custom/entities/<nom>/`. **Le nom du dossier doit être
identique à l'attribut `name` du modèle.**

```
custom/entities/article/
  __init__.py      # vide, obligatoire
  model.py         # table et relations
  methods.py       # logique métier
  routes.py        # routes exposées
  validators/      # optionnel : validation sur mesure
    __init__.py
    post.py        # CreateValidator
    put.py         # UpdateValidator
```

Le plus simple est de créer la table en base puis de lancer
`./cmd.sh generate_entities`, qui écrit ces fichiers. On peut aussi les écrire à la
main, comme ci-dessous. Après tout ajout : redémarrer l'application, puis lancer
`sync_permissions` et attribuer les nouvelles permissions aux rôles.

### `model.py` : la table et ses relations

```python
from app.crud.model import EntityModel, register_model
from app.crud.relationships import ManyToMany, ManyToOne, OneToMany


@register_model
class ArticleModel(EntityModel):
    name = "article"            # nom de l'entité = nom du dossier
    table_name = "articles"     # table PostgreSQL
    # url_prefix = "/blog"      # optionnel, défaut : "/<name>s" -> /articles

    relationships = [
        # articles.author_id -> users.id
        ManyToOne(attribute="author", target="user", foreign_key="author_id"),
        # comments.article_id -> articles.id
        OneToMany(attribute="comments", target="comment", foreign_key="article_id"),
        # table d'association article_tag(article_id, tag_id)
        ManyToMany(
            attribute="tags",
            target="tag",
            association_table="article_tag",
            local_key="article_id",
            remote_key="tag_id",
        ),
    ]

    # Colonnes jamais renvoyées par l'API (restent modifiables en écriture)
    hidden_fields = frozenset({"internal_note", "deleted_at"})
    # Colonnes remplacées par une valeur "***..." lors d'une suppression
    anonymized_fields = ("title",)
```

- Aucune colonne n'est déclarée : elles sont lues dans la table au démarrage.
- `target` est le `name` de l'entité cible, qui doit exister (dans `custom/` ou dans
  le cœur).
- `hidden_fields` et `anonymized_fields` sont des collections : pour un seul élément,
  ne pas oublier la virgule, `("title",)`.

### `methods.py` : la logique métier

La classe doit s'appeler `<Name>Methods`, où `<Name>` est `name.title()`
(`article` → `ArticleMethods`). Elle hérite du CRUD générique ; on ne surcharge que ce
qui change, en appelant `super()`.

```python
from typing import Iterable

from sqlalchemy.orm import Session

from app.crud.methods import BaseCRUDMethods
from app.exceptions import ConflictError


class ArticleMethods(BaseCRUDMethods):
    def create(self, db: Session, data: dict, with_: Iterable[str] = ()):
        data = {**data, "slug": data["title"].strip().lower().replace(" ", "-")}
        return super().create(db, data, with_=with_)

    def publish(self, db: Session, item_id: int):
        article = self.get_or_404(db, item_id)
        if article.published:
            raise ConflictError("Article already published")
        return self.update(db, item_id, {"published": True})
```

Méthodes disponibles : `list`, `get`, `get_or_404`, `create`, `update`, `delete`.
`self.model.python_class` donne la classe ORM pour écrire des requêtes SQLAlchemy.
Les erreurs se lèvent avec les exceptions de `app/exceptions.py` (`NotFoundError`,
`ConflictError`, `ForbiddenError`...), converties automatiquement en réponse JSON.

### `routes.py` : les routes exposées

Le fichier définit `build_router`, qui reçoit le modèle, les méthodes et les
validateurs, et renvoie un `APIRouter`.

```python
from fastapi import Depends
from sqlalchemy.orm import Session

from app.crud.routes import build_crud_router
from app.database import get_db
from app.responses import success_response


def build_router(model, methods, *, create_validator, update_validator):
    router = build_crud_router(
        model,
        methods,
        create_validator=create_validator,
        update_validator=update_validator,
        enabled=("list", "get", "create", "update", "delete"),  # défaut : toutes
    )

    # Route supplémentaire : POST /articles/{item_id}/publish
    @router.post("/{item_id}/publish")
    def publish(item_id: int, db: Session = Depends(get_db)):
        return success_response(methods.publish(db, item_id))

    return router
```

`enabled` restreint les opérations : `("list", "get")` pour une entité en lecture
seule, `()` pour aucune route. Les routes ajoutées sur le router sont, comme les
autres, protégées par l'authentification et par une permission (à créer avec
`sync_permissions`).

Pour une route accessible sans token, la déclarer publique juste après sa
définition :

```python
from app.auth import mark_public

mark_public(f"{model.prefix()}/{{item_id}}/preview", "GET")
```

Dans une route, l'id de l'utilisateur connecté est disponible dans
`request.state.user_id`.

### `validators/` : validation sur mesure (optionnel)

Par défaut, la validation est déduite de la table : en création, les colonnes
NOT NULL sans valeur par défaut sont obligatoires ; en mise à jour, tout est
optionnel. Les relations N:N acceptent une liste d'ids (`"tags": [1, 2]`), qui
remplace l'association existante. Pour aller plus loin, écrire un modèle Pydantic :

```python
# custom/entities/article/validators/post.py
from pydantic import BaseModel, ConfigDict, Field


class CreateValidator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=200)
    content: str
    author_id: int
    tags: list[int] | None = None
```

`validators/put.py` définit de la même façon `UpdateValidator`. Seuls les champs
envoyés sont modifiés.

### Surcharger une entité du cœur

Pour modifier `user`, `role`, `permission`, `usertoken` ou `sendmail`, créer
`custom/entities/<nom>/` : ce dossier **remplace entièrement** celui de
`app/entities/<nom>/`. Copier les fichiers du cœur comme point de départ et les
adapter.

### Utiliser les routes générées

Pour une entité `article`, le CRUD expose :

| Méthode | Route | Action |
|---|---|---|
| `GET` | `/articles` | Liste paginée |
| `QUERY` | `/articles` | Liste avec filtre dans le corps de la requête |
| `POST` | `/articles/query` | Même chose, pour les clients qui ne gèrent pas `QUERY` |
| `GET` | `/articles/{item_id}` | Détail |
| `POST` | `/articles` | Création |
| `PUT` | `/articles/{item_id}` | Mise à jour partielle |
| `DELETE` | `/articles/{item_id}` | Suppression logique |

Paramètres de liste : `limit`, `offset` ou `page` (à partir de 1), `orderby`
(`title.ASC,created_at.DESC`) et `with` (relations à charger : `author,tags`).
`with` est aussi accepté par `get`, `create` et `update`.

Recherche filtrée :

```bash
curl -X POST http://mon-api.local/articles/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
        "filter": "published == true AND (title LIKE '\''python'\'' OR author_id IN (1, 2))",
        "orderby": "created_at.DESC",
        "page": 1,
        "limit": 20,
        "with": ["author", "tags"]
      }'
```

Opérateurs : `==`, `!=`, `<`, `>`, `<=`, `>=`, `IN`, `NOT IN`, `LIKE` (contient,
insensible à la casse), `SOUNDEX` (phonétique), `SIMILAR` (tolérant aux fautes), avec
`AND`, `OR` et des parenthèses. Valeurs : nombres, `'texte'`, `true`/`false`, `null`.

Toutes les réponses ont le même format :

```json
{ "success": true, "data": [ ... ], "meta": { "total": 42, "limit": 20, "offset": 0, "page": 1 } }
{ "success": false, "error": { "code": "not_found", "message": "...", "details": null } }
```

---

## Écrire du code spécifique

Tout le code d'un projet va dans `custom/`, **jamais dans `app/` ni `scripts/`**.
Si un besoin oblige à modifier le cœur, c'est qu'il manque un point d'extension :
il faut l'ajouter dans Sylo, puis publier une nouvelle version.

```
custom/
  __init__.py         # optionnel : hook setup(app)
  entities/           # entités du projet et surcharges des entités du cœur
  mails/              # gabarits d'emails (surcharge et ajout)
  config/             # fichiers de configuration des commandes
  scripts/            # commandes ./cmd.sh propres au projet
  libs/               # modules Python libres
  requirements.txt    # optionnel : dépendances pip supplémentaires
```

| Besoin | Où l'écrire |
|---|---|
| Nouvelle ressource liée à une table | `custom/entities/<nom>/` (voir « Écrire un CRUD ») |
| Règle métier sur une entité (calcul, contrôle, envoi d'email) | `custom/entities/<nom>/methods.py` |
| Route supplémentaire sur une entité | `custom/entities/<nom>/routes.py` |
| Modifier une entité du cœur (`user`...) | `custom/entities/<nom>/` (remplace le cœur) |
| Route transverse, middleware, gestionnaire d'erreur | `custom/__init__.py`, fonction `setup(app)` |
| Classes et fonctions partagées, client d'API externe | `custom/libs/` |
| Email : modifier un gabarit ou en créer un | `custom/mails/` |
| Nouvelle commande en ligne | `custom/scripts/<commande>.py` |
| Bibliothèque Python supplémentaire | `custom/requirements.txt` |

### `custom/__init__.py` : le hook `setup(app)`

Pour ce qui n'est pas lié à une entité, définir `setup` dans `custom/__init__.py`.
La fonction est appelée au démarrage, après l'enregistrement des entités : les
routes ajoutées ici sont protégées par l'authentification et exposées en MCP comme
les autres.

```python
from fastapi import FastAPI


def setup(app: FastAPI) -> None:
    from custom.libs.stats import router as stats_router

    app.include_router(stats_router)
```

### `custom/libs/` : modules libres

Importés par leur chemin complet :

```python
from custom.libs.stats import compute_dashboard
```

### `custom/mails/` : gabarits d'emails

Un gabarit `<nom>` est un dossier contenant `subject.j2`, `html.j2` et, en option,
`txt.j2` (sinon le texte brut est déduit du HTML). Un fichier placé au même chemin
que dans `app/mail/templates/` le remplace (ex : `custom/mails/welcome/html.j2`).
Les images référencées par `<img src="cid:logo">` sont cherchées dans
`custom/mails/<nom>/assets/` puis `custom/mails/assets/`.

Envoi depuis une méthode métier (l'email est aussi enregistré dans `sendmails`) :

```python
from app.mail.service import get_mail_service

get_mail_service().send(
    db,
    to=user.email,
    template="article_published",
    context={"name": user.first_name, "title": article.title},
)
```

### `custom/scripts/` : commandes du projet

`custom/scripts/<commande>.py` se lance avec `./cmd.sh <commande>` ou
`make cmd c="<commande>"`. En tête du script, rendre la racine du projet importable :

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import SessionLocal  # noqa: E402
```

### `custom/requirements.txt`

Dépendances installées par `docker/Dockerfile` lorsque l'image est construite avec
le dossier `custom/` (`make up` dans le dépôt Sylo). L'image publiée, utilisée telle
quelle par le starter, ne les contient pas : un projet qui en a besoin doit
construire sa propre image à partir de `ghcr.io/gerard-loic/sylo`. En local :
`pip install -r custom/requirements.txt`.

---

## Serveur MCP (optionnel)

Avec `SYLO_MCP_ENABLED=true`, un serveur MCP (Streamable HTTP) est monté sur `/mcp`.
Chaque route de l'API devient un outil. Le client MCP envoie le même en-tête
`Authorization: Bearer <token>` que l'API REST, et ne voit que les outils autorisés
par les permissions de l'utilisateur. Les routes à masquer se listent dans le fichier
indiqué par `SYLO_MCP_EXCLUDE_FILE`.

## Journaux

Les erreurs serveur (500) sont écrites dans `logs/<JJMMAAAA>.log`, un fichier par jour.

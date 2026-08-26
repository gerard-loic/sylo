"""Découvre toutes les tables de la base et génère, pour chacune, une entité CRUD
sous `entities/<nom>/` (`__init__.py`, `model.py`, `methods.py`, `routes.py`) : les
relations ManyToOne / OneToMany / ManyToMany sont déduites des clés étrangères
réfléchies en base, y compris les tables d'association N:N.

Une table est traitée comme association N:N (aucune entité créée pour elle, elle
sert uniquement à porter des `ManyToMany` sur les deux entités qu'elle relie) si
elle n'a exactement que 2 colonnes, chacune une clé étrangère simple vers une autre
table (ex: `role_permission(role_id, permission_id)`). Toute autre table dotée d'une
clé primaire simple est traitée comme une entité.

Une table est automatiquement ignorée (aucun fichier écrit) si :
  - son nom (ou son nom singulier) figure dans --exclude-file ;
  - une entité du même nom existe déjà sous `entities/` (sauf --force) ou sous
    `app/entities/` (jamais écrasée, c'est le "coeur" de l'application).

Une relation dont la cible n'est ni générée dans cette exécution, ni déjà
enregistrée (`entities/` ou `app/entities/`) est omise (avec un avertissement) :
`app/crud/mapper.py` planterait au démarrage sur une cible introuvable.

Usage:
    python scripts/generate_entities.py --dry-run
    python scripts/generate_entities.py
    python scripts/generate_entities.py --exclude-file do-not-create.txt
    python scripts/generate_entities.py --exclude-file do-not-create.txt --force

Fichier --exclude-file (une entrée par ligne, lignes vides et '#...' ignorées) :
    users
    roles
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENTITIES_DIR = ROOT / "entities"
CORE_ENTITIES_DIR = ROOT / "app" / "entities"

_KIND_ORDER = {"ManyToOne": 0, "OneToMany": 1, "ManyToMany": 2}


@dataclass
class TableInfo:
    name: str
    columns: list[str]
    pk_columns: list[str]
    # (colonne locale, table référencée, colonne référencée) — clés étrangères simples uniquement
    fks: list[tuple[str, str, str]] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère les entités CRUD manquantes à partir des tables de la base."
    )
    parser.add_argument(
        "--exclude-file",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Fichier listant les tables à ne jamais générer, une par ligne (lignes "
            "vides et commentaires '#...' ignorés). Exemple : do-not-create.txt."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Régénère (écrase model.py/methods.py/routes.py) une entité dont le "
            "dossier existe déjà sous entities/. Sans cette option, une entité déjà "
            "présente est laissée intacte."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit aucun fichier : affiche seulement ce qui serait généré.",
    )
    return parser.parse_args()


def _parse_exclude_file(path: Path) -> set[str]:
    if not path.is_file():
        raise SystemExit(f"--exclude-file : fichier introuvable : {path}")
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def singularize(table_name: str) -> str:
    """Heuristique simple (pas de vraie pluralisation anglaise) : suffisante ici car
    les tables de ce projet suivent toutes le même schéma <racine> + 's' ou 'ies'."""
    if table_name.endswith("ies"):
        return table_name[:-3] + "y"
    if table_name.endswith("s") and not table_name.endswith("ss"):
        return table_name[:-1]
    return table_name


def reflect_tables(engine) -> dict[str, TableInfo]:
    insp = inspect(engine)
    tables: dict[str, TableInfo] = {}
    for table_name in insp.get_table_names():
        columns = [c["name"] for c in insp.get_columns(table_name)]
        pk_columns = insp.get_pk_constraint(table_name).get("constrained_columns") or []
        fks = []
        for fk in insp.get_foreign_keys(table_name):
            constrained = fk.get("constrained_columns") or []
            referred_cols = fk.get("referred_columns") or []
            if len(constrained) != 1 or len(referred_cols) != 1:
                continue  # clé étrangère composite : non supportée par le framework CRUD
            fks.append((constrained[0], fk["referred_table"], referred_cols[0]))
        tables[table_name] = TableInfo(table_name, columns, pk_columns, fks)
    return tables


_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"', re.MULTILINE)
_TABLE_NAME_RE = re.compile(r'^\s*table_name\s*=\s*"([^"]+)"', re.MULTILINE)


def scan_existing_entities(base_dir: Path) -> dict[str, str]:
    """Lit le `table_name` et le `name` réels déclarés dans chaque `model.py` déjà
    présent sous `base_dir` (au lieu de recalculer le nom d'entité depuis le nom de
    table, qui peut diverger d'une entité écrite à la main, ex: table `user_tokens`
    -> entité `usertoken`). Retourne {table_name: entity_name}."""
    result: dict[str, str] = {}
    if not base_dir.is_dir():
        return result
    for entry in sorted(base_dir.iterdir()):
        model_file = entry / "model.py"
        if not entry.is_dir() or entry.name.startswith("__") or not model_file.is_file():
            continue
        content = model_file.read_text(encoding="utf-8")
        name_match = _NAME_RE.search(content)
        table_match = _TABLE_NAME_RE.search(content)
        if name_match and table_match:
            result[table_match.group(1)] = name_match.group(1)
    return result


def is_association_table(info: TableInfo) -> bool:
    if len(info.columns) != 2:
        return False
    return {fk[0] for fk in info.fks} == set(info.columns)


def dedupe_attributes(relationships: list[tuple]) -> list[tuple]:
    """Renomme les attributs en collision (ex: une entité peut avoir à la fois une
    relation directe via colonne FK et une relation N:N vers la même cible) avec un
    suffixe explicite plutôt qu'un simple compteur."""
    seen: set[str] = set()
    result = []
    for kind, attribute, target, extra in relationships:
        candidate = attribute
        if candidate in seen:
            qualifier = extra.get("association_table") or extra.get("foreign_key")
            candidate = f"{attribute}_via_{qualifier}"
        n = 2
        while candidate in seen:
            candidate = f"{attribute}_{n}"
            n += 1
        seen.add(candidate)
        result.append((kind, candidate, target, extra))
    return result


def build_relationships(
    table_name: str,
    tables: dict[str, TableInfo],
    association_tables: dict[str, TableInfo],
    entity_name_of: dict[str, str],
    registered: set[str],
) -> tuple[list[tuple], list[str]]:
    """Retourne (relations, avertissements) pour l'entité portée par `table_name`."""
    info = tables[table_name]
    relationships: list[tuple] = []
    warnings: list[str] = []

    # ManyToOne : une colonne de cette table référence une autre entité.
    for column, target_table, _ in info.fks:
        target_name = entity_name_of.get(target_table)
        if target_name is None or target_name not in registered:
            warnings.append(
                f"relation ManyToOne '{column}' -> '{target_table}' ignorée (cible non enregistrée)"
            )
            continue
        attribute = column[:-3] if column.endswith("_id") else f"{column}_ref"
        relationships.append(("ManyToOne", attribute, target_name, {"foreign_key": column}))

    # OneToMany : une autre entité a une colonne qui référence cette table.
    for other_table, other_info in tables.items():
        if other_table not in entity_name_of:
            continue
        for column, target_table, _ in other_info.fks:
            if target_table != table_name:
                continue
            other_name = entity_name_of[other_table]
            if other_name not in registered:
                warnings.append(
                    f"relation OneToMany '{other_table}' ignorée (cible non enregistrée)"
                )
                continue
            relationships.append(("OneToMany", other_table, other_name, {"foreign_key": column}))

    # ManyToMany : tables d'association reliant cette table à une autre.
    for assoc_name, assoc_info in association_tables.items():
        fks = assoc_info.fks
        for i, own_fk in enumerate(fks):
            if own_fk[1] != table_name:
                continue
            other_fk = fks[1 - i]
            other_name = entity_name_of.get(other_fk[1])
            if other_name is None or other_name not in registered:
                warnings.append(
                    f"relation ManyToMany '{assoc_name}' -> '{other_fk[1]}' ignorée "
                    "(cible non enregistrée)"
                )
                continue
            relationships.append((
                "ManyToMany",
                other_fk[1],
                other_name,
                {
                    "association_table": assoc_name,
                    "local_key": own_fk[0],
                    "remote_key": other_fk[0],
                },
            ))

    relationships = dedupe_attributes(relationships)
    relationships.sort(key=lambda r: (_KIND_ORDER[r[0]], r[1]))
    return relationships, warnings


def render_model(entity_name: str, table_name: str, relationships: list[tuple]) -> str:
    stem = entity_name.title()
    kinds_used = sorted({kind for kind, *_ in relationships})

    lines = []
    for kind, attribute, target, extra in relationships:
        if kind == "ManyToMany":
            lines.append(
                "        ManyToMany(\n"
                f'            attribute="{attribute}",\n'
                f'            target="{target}",\n'
                f'            association_table="{extra["association_table"]}",\n'
                f'            local_key="{extra["local_key"]}",\n'
                f'            remote_key="{extra["remote_key"]}",\n'
                "        ),"
            )
        else:
            lines.append(
                f'        {kind}(attribute="{attribute}", target="{target}", '
                f'foreign_key="{extra["foreign_key"]}"),'
            )

    body = "from app.crud.model import EntityModel, register_model\n"
    if kinds_used:
        body += f"from app.crud.relationships import {', '.join(kinds_used)}\n"
    body += "\n\n"
    body += "@register_model\n"
    body += f"class {stem}Model(EntityModel):\n"
    body += f'    name = "{entity_name}"\n'
    body += f'    table_name = "{table_name}"\n'
    body += f'    url_prefix = "/{table_name}"\n'
    body += "    relationships = [\n"
    if lines:
        body += "\n".join(lines) + "\n"
    body += "    ]\n"
    return body


def render_methods(entity_name: str) -> str:
    stem = entity_name.title()
    return (
        "from app.crud.methods import BaseCRUDMethods\n"
        "\n\n"
        f"class {stem}Methods(BaseCRUDMethods):\n"
        "    pass\n"
    )


def render_routes() -> str:
    return (
        "from app.crud.routes import build_crud_router\n"
        "\n\n"
        "def build_router(model, methods, *, create_validator, update_validator):\n"
        "    return build_crud_router(\n"
        "        model, methods, create_validator=create_validator, update_validator=update_validator\n"
        "    )\n"
    )


def main() -> None:
    args = parse_args()

    excluded = _parse_exclude_file(args.exclude_file) if args.exclude_file else set()

    from app.database import engine

    print("Réflexion des tables...")
    tables = reflect_tables(engine)

    association_tables = {name: t for name, t in tables.items() if is_association_table(t)}
    candidate_entity_tables = {name: t for name, t in tables.items() if name not in association_tables}

    entity_tables: dict[str, TableInfo] = {}
    for name, info in candidate_entity_tables.items():
        if not info.pk_columns:
            print(f"  ! {name} : pas de clé primaire, table ignorée")
            continue
        if len(info.pk_columns) > 1:
            print(f"  ! {name} : clé primaire composite, table ignorée")
            continue
        entity_tables[name] = info

    # Détecte les collisions de nom singulier (ex: deux tables qui se réduiraient au même nom).
    entity_name_of: dict[str, str] = {}
    owner_of: dict[str, str] = {}
    for table_name in sorted(entity_tables):
        entity_name = singularize(table_name)
        if entity_name in owner_of:
            print(
                f"  ! collision : '{table_name}' et '{owner_of[entity_name]}' donnent "
                f"toutes deux '{entity_name}', les deux tables sont ignorées"
            )
            entity_name_of.pop(owner_of[entity_name], None)
            continue
        owner_of[entity_name] = table_name
        entity_name_of[table_name] = entity_name

    # Détectées par lecture des model.py existants (table_name réel), pas par nom
    # recalculé : une entité écrite à la main peut ne pas suivre la même convention
    # de nommage que `singularize()` (ex: table `user_tokens` -> entité `usertoken`).
    core_by_table = scan_existing_entities(CORE_ENTITIES_DIR)
    override_by_table = scan_existing_entities(ENTITIES_DIR)

    to_generate: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    for table_name, entity_name in entity_name_of.items():
        if table_name in excluded or entity_name in excluded:
            skipped.append((table_name, "exclue (--exclude-file)"))
            continue
        if table_name in core_by_table:
            skipped.append((table_name, f"déjà implémentée dans app/entities/{core_by_table[table_name]}/"))
            continue
        if table_name in override_by_table and not args.force:
            skipped.append((
                table_name,
                f"déjà implémentée dans entities/{override_by_table[table_name]}/ (utiliser --force pour régénérer)",
            ))
            continue
        to_generate[table_name] = entity_name

    # Toute entité déjà enregistrée (core ou override) ou générée dans cette exécution
    # peut servir de cible de relation ; les autres tables valides mais non générées
    # (exclues, sans implémentation existante) ne le peuvent pas.
    registered = set(core_by_table.values()) | set(override_by_table.values()) | set(to_generate.values())

    print(f"\nTables trouvées           : {len(tables)}")
    print(f"Tables d'association N:N  : {len(association_tables)} ({', '.join(sorted(association_tables)) or '-'})")
    print(f"Entités candidates        : {len(entity_tables)}")
    print(f"Ignorées                  : {len(skipped)}")
    for table_name, reason in skipped:
        print(f"  - {table_name} : {reason}")
    print(f"À générer                 : {len(to_generate)}")

    if not to_generate:
        return

    plan: dict[str, tuple[str, list[tuple], list[str]]] = {}
    for table_name, entity_name in sorted(to_generate.items()):
        relationships, warnings = build_relationships(
            table_name, tables, association_tables, entity_name_of, registered
        )
        plan[table_name] = (entity_name, relationships, warnings)

    for table_name, (entity_name, relationships, warnings) in plan.items():
        summary = ", ".join(f"{kind}:{attribute}->{target}" for kind, attribute, target, _ in relationships)
        print(f"\n  + {entity_name}  (table {table_name})")
        print(f"      relations : {summary or '-'}")
        for warning in warnings:
            print(f"      ! {warning}")

    if args.dry_run:
        print("\n--dry-run : aucun fichier écrit.")
        return

    for table_name, (entity_name, relationships, _warnings) in plan.items():
        entity_dir = ENTITIES_DIR / entity_name
        entity_dir.mkdir(parents=True, exist_ok=True)
        init_file = entity_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("", encoding="utf-8")
        (entity_dir / "model.py").write_text(render_model(entity_name, table_name, relationships), encoding="utf-8")
        (entity_dir / "methods.py").write_text(render_methods(entity_name), encoding="utf-8")
        (entity_dir / "routes.py").write_text(render_routes(), encoding="utf-8")

    print(f"\n{len(plan)} entité(s) générée(s) sous entities/.")


if __name__ == "__main__":
    main()

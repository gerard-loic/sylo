from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.exceptions import NotFoundError
from app.security import anonymized_value
from app.crud.mapper import get_association_table
from app.crud.model import EntityModel
from app.crud.relationships import ManyToMany


class BaseCRUDMethods:
    """Logique CRUD générique, instanciée pour chaque entité. À hériter dans
    `entities/<nom>/methods.py` pour surcharger uniquement ce qui doit changer
    (ex: hashage avant create, règles métier supplémentaires) en rappelant `super()`.
    """

    def __init__(self, model: type[EntityModel]):
        self.model = model

    def list(
        self,
        db: Session,
        *,
        limit: int,
        offset: int,
        with_: Iterable[str] = (),
        filter_clause: ColumnElement | None = None,
        order_by: Iterable[ColumnElement] | None = None,
    ) -> tuple[list, int]:
        base_query = db.query(self.model.python_class).filter(self._not_deleted())
        if filter_clause is not None:
            base_query = base_query.filter(filter_clause)
        total = base_query.count()
        # La clé primaire est toujours ajoutée en dernier critère : départage stable
        # pour la pagination, même si `order_by` ne suffit pas à trier de façon unique
        # (et ordre par défaut si `order_by` n'est pas fourni).
        order_clauses = [*order_by, self.model.pk_column] if order_by else [self.model.pk_column]
        items = (
            self._with_relationships(base_query, with_)
            .order_by(*order_clauses)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return items, total

    def get(self, db: Session, item_id: Any, with_: Iterable[str] = ()) -> Any | None:
        query = self._with_relationships(db.query(self.model.python_class), with_)
        return query.filter(self.model.pk_column == item_id, self._not_deleted()).first()

    def get_or_404(self, db: Session, item_id: Any, with_: Iterable[str] = ()) -> Any:
        obj = self.get(db, item_id, with_)
        if obj is None:
            raise NotFoundError(self.model.name, item_id)
        return obj

    def create(self, db: Session, data: dict, with_: Iterable[str] = ()) -> Any:
        column_data, m2m_data = self._split_many_to_many(data)
        obj = self.model.python_class(**column_data)
        db.add(obj)
        db.flush()
        item_id = getattr(obj, self.model.pk_name())
        self._sync_many_to_many(db, item_id, m2m_data)
        db.commit()
        return self.get(db, item_id, with_)

    def update(self, db: Session, item_id: Any, data: dict, with_: Iterable[str] = ()) -> Any:
        column_data, m2m_data = self._split_many_to_many(data)
        self._get_raw_or_404(db, item_id, update=column_data)
        self._sync_many_to_many(db, item_id, m2m_data)
        db.commit()
        return self.get(db, item_id, with_)

    def delete(self, db: Session, item_id: Any) -> None:
        """Suppression logique : la ligne n'est pas retirée de la base, seul
        `deleted_at` est horodaté (elle disparaît alors des `list`/`get`, qui
        filtrent sur `_not_deleted()`). Les colonnes déclarées dans
        `model.anonymized_fields` voient leur valeur remplacée par une chaîne
        unique préfixée par `***`.
        """
        obj = self._get_raw_or_404(db, item_id)
        obj.deleted_at = datetime.utcnow()
        for field in self.model.anonymized_fields:
            setattr(obj, field, anonymized_value())
        db.commit()

    def _get_raw_or_404(self, db: Session, item_id: Any, *, update: dict | None = None) -> Any:
        obj = (
            db.query(self.model.python_class)
            .filter(self.model.pk_column == item_id, self._not_deleted())
            .first()
        )
        if obj is None:
            raise NotFoundError(self.model.name, item_id)
        if update:
            for key, value in update.items():
                setattr(obj, key, value)
        return obj

    def _not_deleted(self) -> ColumnElement:
        return self.model.python_class.deleted_at.is_(None)

    def _with_relationships(self, query, with_: Iterable[str] = ()):
        requested = set(with_)
        if not requested:
            return query
        for rel in self.model.relationships:
            if rel.attribute in requested:
                query = query.options(selectinload(getattr(self.model.python_class, rel.attribute)))
        return query

    def _split_many_to_many(self, data: dict) -> tuple[dict, dict]:
        """Sépare `data` entre colonnes (passées telles quelles) et relations N:N
        (retirées : les relations sont `viewonly` côté ORM, les écrire directement
        via le constructeur/`setattr` n'aurait silencieusement aucun effet en base).
        """
        m2m_by_attribute = {
            rel.attribute: rel for rel in self.model.relationships if isinstance(rel, ManyToMany)
        }
        column_data = {key: value for key, value in data.items() if key not in m2m_by_attribute}
        m2m_data = {
            key: (m2m_by_attribute[key], value) for key, value in data.items() if key in m2m_by_attribute
        }
        return column_data, m2m_data

    def _sync_many_to_many(self, db: Session, item_id: Any, m2m_data: dict) -> None:
        for rel, remote_ids in m2m_data.values():
            if remote_ids is None:
                continue
            table = get_association_table(rel.association_table)
            db.execute(delete(table).where(table.c[rel.local_key] == item_id))
            if remote_ids:
                db.execute(
                    insert(table),
                    [{rel.local_key: item_id, rel.remote_key: rid} for rid in remote_ids],
                )

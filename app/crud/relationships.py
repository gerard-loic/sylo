from dataclasses import dataclass


@dataclass(frozen=True)
class OneToMany:
    """Côté "1" d'une relation 1:N : cette entité a plusieurs entités `target` liées
    via la colonne étrangère `foreign_key` portée par la table de `target`."""

    attribute: str
    target: str
    foreign_key: str


@dataclass(frozen=True)
class ManyToOne:
    """Côté "N" d'une relation 1:N : cette entité référence une seule entité `target`
    via sa propre colonne étrangère `foreign_key`."""

    attribute: str
    target: str
    foreign_key: str


@dataclass(frozen=True)
class ManyToMany:
    """Relation N:N portée par une table d'association `association_table`, dont
    `local_key` référence cette entité et `remote_key` référence `target`."""

    attribute: str
    target: str
    association_table: str
    local_key: str
    remote_key: str

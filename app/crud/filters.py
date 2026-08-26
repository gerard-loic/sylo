import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.crud.columns import python_type
from app.crud.model import EntityModel
from app.exceptions import InvalidFilterError

# Grammaire (AND se lie plus fort que OR, comme en SQL) :
#   or_expr    := and_expr ("OR" and_expr)*
#   and_expr   := atom ("AND" atom)*
#   atom       := "(" or_expr ")" | comparison
#   comparison := IDENT operator value
#   operator   := "==" | "!=" | "<=" | ">=" | "<" | ">" | "IN" | "NOT" "IN" | "LIKE"
#   value      := STRING | NUMBER | "true" | "false" | "null" | "(" value ("," value)* ")"
#
# LIKE fait une recherche approximative insensible à la casse (ex: `name LIKE 'ali'`
# trouve "Alice") : la valeur est traitée comme du texte brut, pas un motif SQL — les
# caractères spéciaux `%`/`_` qu'elle contiendrait sont échappés avant d'être entourés
# de `%` (voir `_escape_like`), uniquement utilisable sur des champs texte.

_KEYWORDS = {"AND", "OR", "IN", "NOT", "LIKE", "TRUE", "FALSE", "NULL"}

_TOKEN_RE = re.compile(
    r"""
    (?P<WS>\s+)
    |(?P<STRING>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    |(?P<NUMBER>-?\d+(?:\.\d+)?)
    |(?P<OP>==|!=|<=|>=|<|>)
    |(?P<LPAREN>\()
    |(?P<RPAREN>\))
    |(?P<COMMA>,)
    |(?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class Token:
    kind: str
    value: Any


@dataclass(frozen=True)
class Comparison:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class BoolOp:
    op: str  # "AND" | "OR"
    clauses: tuple


def _unescape_string(raw: str) -> str:
    return re.sub(r"\\(.)", r"\1", raw[1:-1])


def _tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    pos, length = 0, len(source)
    while pos < length:
        match = _TOKEN_RE.match(source, pos)
        if match is None:
            raise InvalidFilterError(
                f"Caractère inattendu dans le filtre à la position {pos}: {source[pos]!r}"
            )
        kind = match.lastgroup
        text = match.group()
        pos = match.end()
        if kind == "WS":
            continue
        if kind == "STRING":
            tokens.append(Token("STRING", _unescape_string(text)))
        elif kind == "NUMBER":
            tokens.append(Token("NUMBER", float(text) if "." in text else int(text)))
        elif kind == "IDENT":
            upper = text.upper()
            if upper == "TRUE":
                tokens.append(Token("BOOL", True))
            elif upper == "FALSE":
                tokens.append(Token("BOOL", False))
            elif upper == "NULL":
                tokens.append(Token("NULL", None))
            elif upper in _KEYWORDS:
                tokens.append(Token(upper, upper))
            else:
                tokens.append(Token("IDENT", text))
        else:
            tokens.append(Token(kind, text))
    tokens.append(Token("EOF", None))
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token]):
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> "Comparison | BoolOp":
        node = self._or_expr()
        self._expect("EOF")
        return node

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expect(self, kind: str) -> Token:
        token = self._peek()
        if token.kind != kind:
            raise InvalidFilterError(
                f"Filtre invalide : {kind} attendu, {token.kind} trouvé ({token.value!r})."
            )
        return self._advance()

    def _or_expr(self) -> "Comparison | BoolOp":
        clauses = [self._and_expr()]
        while self._peek().kind == "OR":
            self._advance()
            clauses.append(self._and_expr())
        return clauses[0] if len(clauses) == 1 else BoolOp("OR", tuple(clauses))

    def _and_expr(self) -> "Comparison | BoolOp":
        clauses = [self._atom()]
        while self._peek().kind == "AND":
            self._advance()
            clauses.append(self._atom())
        return clauses[0] if len(clauses) == 1 else BoolOp("AND", tuple(clauses))

    def _atom(self) -> "Comparison | BoolOp":
        if self._peek().kind == "LPAREN":
            self._advance()
            node = self._or_expr()
            self._expect("RPAREN")
            return node
        return self._comparison()

    def _comparison(self) -> Comparison:
        field = self._expect("IDENT").value
        op = self._operator()
        value = self._value(op)
        return Comparison(field, op, value)

    def _operator(self) -> str:
        token = self._peek()
        if token.kind == "OP":
            self._advance()
            return token.value
        if token.kind == "IN":
            self._advance()
            return "IN"
        if token.kind == "NOT":
            self._advance()
            self._expect("IN")
            return "NOT IN"
        if token.kind == "LIKE":
            self._advance()
            return "LIKE"
        raise InvalidFilterError(f"Opérateur attendu, trouvé {token.kind} ({token.value!r}).")

    def _value(self, op: str) -> Any:
        if op in ("IN", "NOT IN"):
            self._expect("LPAREN")
            values = [self._scalar()]
            while self._peek().kind == "COMMA":
                self._advance()
                values.append(self._scalar())
            self._expect("RPAREN")
            return values
        return self._scalar()

    def _scalar(self) -> Any:
        token = self._advance()
        if token.kind in ("STRING", "NUMBER", "BOOL", "NULL"):
            return token.value
        raise InvalidFilterError(f"Valeur attendue, trouvée {token.kind} ({token.value!r}).")


def parse_filter(source: str) -> "Comparison | BoolOp":
    tokens = _tokenize(source)
    if len(tokens) == 1:
        raise InvalidFilterError("Le filtre est vide.")
    return _Parser(tokens).parse()


def _coerce(column, raw: Any) -> Any:
    if raw is None:
        return None
    py_type = python_type(column)
    if py_type is bool:
        if isinstance(raw, bool):
            return raw
        raise InvalidFilterError(f"Valeur booléenne attendue pour '{column.name}', reçu {raw!r}.")
    if isinstance(raw, bool):
        raise InvalidFilterError(f"Valeur inattendue pour '{column.name}' : {raw!r} n'est pas comparable à ce champ.")
    if py_type in (int, float, Decimal):
        if not isinstance(raw, (int, float)):
            raise InvalidFilterError(f"Valeur numérique attendue pour '{column.name}', reçu {raw!r}.")
        try:
            return py_type(raw)
        except (TypeError, ValueError, InvalidOperation):
            raise InvalidFilterError(f"Valeur numérique invalide pour '{column.name}' : {raw!r}.")
    if py_type is datetime:
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            raise InvalidFilterError(f"Date/heure invalide pour '{column.name}' : {raw!r}.")
    if py_type is date:
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            raise InvalidFilterError(f"Date invalide pour '{column.name}' : {raw!r}.")
    return str(raw)


def _escape_like(value: str) -> str:
    """Échappe les caractères spéciaux LIKE (`%`, `_`) pour que la recherche porte sur
    le texte tel quel, pas sur un motif SQL."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _compile(node: "Comparison | BoolOp", model: type[EntityModel]) -> ColumnElement:
    if isinstance(node, BoolOp):
        clauses = [_compile(clause, model) for clause in node.clauses]
        return and_(*clauses) if node.op == "AND" else or_(*clauses)

    if node.field not in model.table.columns:
        raise InvalidFilterError(f"Champ de filtre inconnu pour '{model.name}' : {node.field!r}.")
    column = model.table.columns[node.field]

    if node.op in ("IN", "NOT IN"):
        values = [_coerce(column, value) for value in node.value]
        return column.notin_(values) if node.op == "NOT IN" else column.in_(values)

    if node.op == "LIKE":
        if python_type(column) is not str:
            raise InvalidFilterError(
                f"L'opérateur LIKE n'est utilisable que sur des champs texte ('{column.name}' n'en est pas un)."
            )
        if not isinstance(node.value, str):
            raise InvalidFilterError(f"L'opérateur LIKE attend une valeur texte pour '{column.name}'.")
        return column.ilike(f"%{_escape_like(node.value)}%", escape="\\")

    value = _coerce(column, node.value)
    if node.op == "==":
        return column.is_(None) if value is None else column == value
    if node.op == "!=":
        return column.isnot(None) if value is None else column != value
    if node.op == "<":
        return column < value
    if node.op == ">":
        return column > value
    if node.op == "<=":
        return column <= value
    if node.op == ">=":
        return column >= value
    raise InvalidFilterError(f"Opérateur non supporté : {node.op!r}.")  # pragma: no cover


def compile_filter(source: str, model: type[EntityModel]) -> ColumnElement:
    """Parse une expression de filtre (ex: `status == 'active' AND (age >= 18 OR role IN
    ('admin', 'owner') OR name LIKE 'ali')`) et la compile en clause SQLAlchemy filtrable
    sur `model`.
    """
    return _compile(parse_filter(source), model)

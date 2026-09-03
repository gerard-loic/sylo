import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, func, or_
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
#                 | "SOUNDEX" | "SIMILAR"
#   value      := STRING | NUMBER | "true" | "false" | "null" | "(" value ("," value)* ")"
#
# LIKE fait une recherche approximative insensible à la casse (ex: `name LIKE 'ali'`
# trouve "Alice") : la valeur est traitée comme du texte brut, pas un motif SQL — les
# caractères spéciaux `%`/`_` qu'elle contiendrait sont échappés avant d'être entourés
# de `%` (voir `_escape_like`), uniquement utilisable sur des champs texte.
#
# SOUNDEX fait une recherche phonétique (ex: `name SOUNDEX 'Alisse'` trouve "Alice") :
# la proximité est mesurée par `difference()` de l'extension PostgreSQL `fuzzystrmatch`
# (score 0..4), le match est retenu à partir de `_SOUNDEX_MIN_SCORE`. Requiert
# `CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;` sur la base, uniquement sur des
# champs texte. Le codage Soundex est calibré pour l'anglais et ignore les accents.
#
# SIMILAR fait une recherche lexicale tolérante aux fautes de frappe (ex:
# `name SIMILAR 'Alisse'` trouve "Alice") : la proximité est mesurée par `similarity()`
# de l'extension PostgreSQL `pg_trgm` (score 0..1, sur les trigrammes), le match est
# retenu à partir de `_SIMILARITY_MIN_SCORE`. Requiert
# `CREATE EXTENSION IF NOT EXISTS pg_trgm;` sur la base, uniquement sur des champs
# texte. Insensible à la casse, mais pas aux accents (combiner avec `unaccent` au
# besoin).

_KEYWORDS = {"AND", "OR", "IN", "NOT", "LIKE", "SOUNDEX", "SIMILAR", "TRUE", "FALSE", "NULL"}

# Score minimal de `difference()` (0..4) pour qu'une comparaison SOUNDEX matche.
_SOUNDEX_MIN_SCORE = 3

# Score minimal de `similarity()` (0..1) pour qu'une comparaison SIMILAR matche
# (0.3 = seuil par défaut de pg_trgm).
_SIMILARITY_MIN_SCORE = 0.3

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
        if token.kind in ("LIKE", "SOUNDEX", "SIMILAR"):
            self._advance()
            return token.kind
        raise InvalidFilterError(f"Operand expected, found {token.kind} ({token.value!r}).")

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
        raise InvalidFilterError(f"Value expected, found {token.kind} ({token.value!r}).")


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
        raise InvalidFilterError(f"Boolean value expected for '{column.name}', reçu {raw!r}.")
    if isinstance(raw, bool):
        raise InvalidFilterError(f"Unexpected value for '{column.name}' : {raw!r} n'est pas comparable à ce champ.")
    if py_type in (int, float, Decimal):
        if not isinstance(raw, (int, float)):
            raise InvalidFilterError(f"Numeric value expected for '{column.name}', reçu {raw!r}.")
        try:
            return py_type(raw)
        except (TypeError, ValueError, InvalidOperation):
            raise InvalidFilterError(f"Invalid numeric value for '{column.name}' : {raw!r}.")
    if py_type is datetime:
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            raise InvalidFilterError(f"Invalid date/time for '{column.name}' : {raw!r}.")
    if py_type is date:
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            raise InvalidFilterError(f"Invalid date for '{column.name}' : {raw!r}.")
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
        raise InvalidFilterError(f"Unknown filter field for '{model.name}' : {node.field!r}.")
    column = model.table.columns[node.field]

    if node.op in ("IN", "NOT IN"):
        values = [_coerce(column, value) for value in node.value]
        return column.notin_(values) if node.op == "NOT IN" else column.in_(values)

    if node.op == "LIKE":
        if python_type(column) is not str:
            raise InvalidFilterError(
                f"LIKE operand is only appliable on text fields ('{column.name}' n'en est pas un)."
            )
        if not isinstance(node.value, str):
            raise InvalidFilterError(f"LIKE operand requires a text value for '{column.name}'.")
        return column.ilike(f"%{_escape_like(node.value)}%", escape="\\")

    if node.op == "SOUNDEX":
        if python_type(column) is not str:
            raise InvalidFilterError(
                f"SOUNDEX operand is only appliable on text fields ('{column.name}' n'en est pas un)."
            )
        if not isinstance(node.value, str):
            raise InvalidFilterError(f"SOUNDEX operand requires a text value for '{column.name}'.")
        return func.difference(column, node.value) >= _SOUNDEX_MIN_SCORE

    if node.op == "SIMILAR":
        if python_type(column) is not str:
            raise InvalidFilterError(
                f"SIMILAR operand is only appliable on text fields ('{column.name}' n'en est pas un)."
            )
        if not isinstance(node.value, str):
            raise InvalidFilterError(f"SIMILAR operand requires a text value for '{column.name}'.")
        return func.similarity(column, node.value) >= _SIMILARITY_MIN_SCORE

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
    raise InvalidFilterError(f"Non supported operand : {node.op!r}.")  # pragma: no cover


def compile_filter(source: str, model: type[EntityModel]) -> ColumnElement:
    """Parse une expression de filtre (ex: `status == 'active' AND (age >= 18 OR role IN
    ('admin', 'owner') OR name LIKE 'ali' OR name SOUNDEX 'Alisse' OR name SIMILAR
    'Alisse')`) et la compile en clause SQLAlchemy filtrable sur `model`.
    """
    return _compile(parse_filter(source), model)

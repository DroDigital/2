"""Pratt parser turning expression text into a :mod:`rulelens.nodes` tree.

Grammar, lowest to highest precedence::

    or  <  and  <  not  <  comparison  <  + -  <  * / %  <  unary -  <  call / field

Comparisons (``== != < <= > >= in not in is null is not null``) do not chain:
``a < b < c`` is an error, because its meaning is ambiguous between languages.
"""

from __future__ import annotations

from typing import Final

from .errors import ExpressionError
from .lexer import Token, tokenize
from .nodes import (
    PREC_ADD,
    PREC_AND,
    PREC_COMPARE,
    PREC_MUL,
    PREC_NOT,
    PREC_OR,
    PREC_UNARY,
    Binary,
    Call,
    Field,
    IsNull,
    ListLiteral,
    Literal,
    Logical,
    Node,
    Unary,
)

MAX_DEPTH: Final = 48

_COMPARISONS: Final = frozenset({"==", "!=", "<", "<=", ">", ">="})
_INFIX_PREC: Final = {
    "+": PREC_ADD,
    "-": PREC_ADD,
    "*": PREC_MUL,
    "/": PREC_MUL,
    "%": PREC_MUL,
}


def parse(source: str) -> Node:
    """Parse ``source`` into an expression tree.

    Raises:
        ExpressionError: on any lexical or syntactic problem.
    """
    return _Parser(source).parse_all()


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = tokenize(source)
        self.pos = 0
        self.depth = 0

    # -- token helpers -----------------------------------------------------
    def peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def error(self, message: str, tok: Token | None = None) -> ExpressionError:
        tok = tok or self.peek()
        return ExpressionError(message, self.source, tok.start, max(tok.end, tok.start + 1))

    def expect(self, kind: str, what: str) -> Token:
        tok = self.peek()
        if tok.kind != kind:
            raise self.error(f"expected {what}, found {self._describe(tok)}")
        return self.advance()

    @staticmethod
    def _describe(tok: Token) -> str:
        return "end of expression" if tok.kind == "EOF" else repr(tok.text)

    def _is_op(self, tok: Token, *ops: str) -> bool:
        return tok.kind == "OP" and tok.text in ops

    def _is_kw(self, tok: Token, *words: str) -> bool:
        return tok.kind == "KEYWORD" and tok.text in words

    # -- grammar -----------------------------------------------------------
    def parse_all(self) -> Node:
        if self.peek().kind == "EOF":
            raise self.error("expression is empty")
        node = self.parse_expr(PREC_OR)
        tok = self.peek()
        if tok.kind != "EOF":
            raise self.error(f"unexpected {self._describe(tok)}")
        return node

    def parse_expr(self, min_prec: int) -> Node:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self.error(f"expression is nested too deeply (limit {MAX_DEPTH})")
        try:
            left = self.parse_prefix()
            while True:
                nxt = self._parse_infix(left, min_prec)
                if nxt is None:
                    return left
                left = nxt
        finally:
            self.depth -= 1

    def _parse_infix(self, left: Node, min_prec: int) -> Node | None:
        tok = self.peek()
        start = left.span[0]

        if self._is_kw(tok, "or") and min_prec <= PREC_OR:
            self.advance()
            right = self.parse_expr(PREC_OR + 1)
            return Logical("or", left, right, span=(start, right.span[1]))
        if self._is_kw(tok, "and") and min_prec <= PREC_AND:
            self.advance()
            right = self.parse_expr(PREC_AND + 1)
            return Logical("and", left, right, span=(start, right.span[1]))

        if min_prec <= PREC_COMPARE and self._starts_comparison(tok):
            node = self._parse_comparison(left)
            if self._starts_comparison(self.peek()):
                raise self.error("chained comparisons are not supported; combine them with 'and'")
            return node

        if tok.kind == "OP" and tok.text in _INFIX_PREC and _INFIX_PREC[tok.text] >= min_prec:
            prec = _INFIX_PREC[tok.text]
            self.advance()
            right = self.parse_expr(prec + 1)
            return Binary(tok.text, left, right, span=(start, right.span[1]))
        return None

    def _starts_comparison(self, tok: Token) -> bool:
        if self._is_op(tok, *_COMPARISONS) or self._is_kw(tok, "in", "is"):
            return True
        return self._is_kw(tok, "not") and self._is_kw(self.peek(1), "in")

    def _parse_comparison(self, left: Node) -> Node:
        tok = self.advance()
        start = left.span[0]
        if tok.kind == "OP":
            right = self.parse_expr(PREC_COMPARE + 1)
            return Binary(tok.text, left, right, span=(start, right.span[1]))
        if tok.text == "in":
            right = self.parse_expr(PREC_COMPARE + 1)
            return Binary("in", left, right, span=(start, right.span[1]))
        if tok.text == "not":  # "not in"
            self.advance()  # the 'in'
            right = self.parse_expr(PREC_COMPARE + 1)
            inner = Binary("in", left, right, span=(start, right.span[1]))
            return Unary("not", inner, span=inner.span)
        # "is null" / "is not null"
        negated = False
        if self._is_kw(self.peek(), "not"):
            self.advance()
            negated = True
        end_tok = self.peek()
        if not self._is_kw(end_tok, "null"):
            raise self.error("expected 'null' after 'is'", end_tok)
        self.advance()
        return IsNull(left, negated, span=(start, end_tok.end))

    def parse_prefix(self) -> Node:
        tok = self.peek()
        if tok.kind == "NUMBER":
            self.advance()
            return Literal(tok.value, span=(tok.start, tok.end))  # type: ignore[arg-type]
        if tok.kind == "STRING":
            self.advance()
            return Literal(tok.value, span=(tok.start, tok.end))  # type: ignore[arg-type]
        if tok.kind == "KEYWORD":
            return self._parse_keyword(tok)
        if tok.kind == "IDENT":
            return self._parse_name()
        if tok.kind == "LPAREN":
            self.advance()
            inner = self.parse_expr(PREC_OR)
            close = self.expect("RPAREN", "')'")
            # Keep inner structure but widen the span to include the parentheses.
            return _with_span(inner, (tok.start, close.end))
        if tok.kind == "LBRACKET":
            return self._parse_list()
        if self._is_op(tok, "-"):
            self.advance()
            operand = self.parse_expr(PREC_UNARY)
            span = (tok.start, operand.span[1])
            if (
                isinstance(operand, Literal)
                and isinstance(operand.value, int | float)
                and not isinstance(operand.value, bool)
            ):
                return Literal(-operand.value, span=span)
            return Unary("-", operand, span=span)
        raise self.error(f"expected a value, found {self._describe(tok)}")

    def _parse_keyword(self, tok: Token) -> Node:
        span = (tok.start, tok.end)
        if tok.text == "true":
            self.advance()
            return Literal(True, span=span)
        if tok.text == "false":
            self.advance()
            return Literal(False, span=span)
        if tok.text == "null":
            self.advance()
            return Literal(None, span=span)
        if tok.text == "not":
            self.advance()
            operand = self.parse_expr(PREC_NOT)
            return Unary("not", operand, span=(tok.start, operand.span[1]))
        raise self.error(f"unexpected keyword {tok.text!r}")

    def _parse_name(self) -> Node:
        first = self.advance()
        if self.peek().kind == "LPAREN":
            return self._parse_call(first)
        path = [first.text]
        end = first.end
        while self.peek().kind == "DOT":
            self.advance()
            part = self.expect("IDENT", "a field name after '.'")
            path.append(part.text)
            end = part.end
        return Field(tuple(path), span=(first.start, end))

    def _parse_call(self, name: Token) -> Node:
        self.advance()  # '('
        args: list[Node] = []
        if self.peek().kind != "RPAREN":
            while True:
                args.append(self.parse_expr(PREC_OR))
                if self.peek().kind == "COMMA":
                    self.advance()
                    continue
                break
        close = self.expect("RPAREN", "')' to close the call")
        return Call(name.text, tuple(args), span=(name.start, close.end))

    def _parse_list(self) -> Node:
        open_tok = self.advance()
        items: list[Node] = []
        if self.peek().kind != "RBRACKET":
            while True:
                items.append(self.parse_expr(PREC_OR))
                if self.peek().kind == "COMMA":
                    self.advance()
                    continue
                break
        close = self.expect("RBRACKET", "']'")
        return ListLiteral(tuple(items), span=(open_tok.start, close.end))


def _with_span(node: Node, span: tuple[int, int]) -> Node:
    """Return ``node`` re-spanned to ``span`` (used to include enclosing parentheses)."""
    match node:
        case Literal(value=v):
            return Literal(v, span=span)
        case ListLiteral(items=items):
            return ListLiteral(items, span=span)
        case Field(path=path):
            return Field(path, span=span)
        case Unary(op=op, operand=operand):
            return Unary(op, operand, span=span)
        case Binary(op=op, left=left, right=right):
            return Binary(op, left, right, span=span)
        case Logical(op=op, left=left, right=right):
            return Logical(op, left, right, span=span)
        case IsNull(operand=operand, negated=negated):
            return IsNull(operand, negated, span=span)
        case Call(name=name, args=args):
            return Call(name, args, span=span)
    raise TypeError(f"unknown node {node!r}")  # pragma: no cover

"""calculator tool — low risk, no approval.

Deliberately does NOT use eval()/exec(). Only a fixed set of arithmetic
AST nodes are permitted, so a malicious or malformed "expression" argument
can't do anything beyond arithmetic — this matters because tool arguments
are model-produced text and must be treated as untrusted input.
"""

import ast
import operator

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_ALLOWED_UNARYOPS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


class UnsafeExpressionError(ValueError):
    pass


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise UnsafeExpressionError(f"Non-numeric constant: {node.value!r}")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](
            _eval_node(node.left), _eval_node(node.right)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    raise UnsafeExpressionError(f"Disallowed expression node: {type(node).__name__}")


def run(expression: str) -> dict:
    """Contract: input_schema={expression: str} -> output_schema={result: number}"""
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree.body)
    except (SyntaxError, UnsafeExpressionError, ZeroDivisionError) as exc:
        raise ValueError(f"Could not evaluate '{expression}': {exc}") from exc
    return {"result": result}
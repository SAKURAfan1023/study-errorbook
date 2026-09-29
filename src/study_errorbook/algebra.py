"""Bounded exact polynomial identities and linear equations, without eval."""

import ast
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from .models import DomainError


def bounded_coefficients(result):
    if any(v.numerator.bit_length() > 4096 or v.denominator.bit_length() > 4096 for v in result.values()):
        raise DomainError("unsupported_expression", "中间系数的分子或分母超过4096个二进制位计算上限；请人工核对或拆分算式，不会近似验算。")
    return result


def polynomial(expression: str, *, allow_variable: bool = True) -> dict[int, Fraction]:
    if len(expression) > 300:
        raise DomainError("unsupported_expression", "算式长度超过上限。")
    source = expression.replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise DomainError(
            "unsupported_expression", "仅支持显式乘号、变量 x 与有理数多项式。"
        ) from None
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise DomainError("unsupported_expression", "算式过于复杂。")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            if isinstance(node.value, float):
                # AST floats may already be rounded or underflowed. Read the original token.
                try:
                    exact = Decimal(ast.get_source_segment(source, node))
                except InvalidOperation:
                    raise DomainError("unsupported_expression", "数字超出验证范围。") from None
                # Bound the magnitude before constructing a potentially enormous Fraction.
                magnitude = exact.copy_abs()
                if not exact.is_finite() or magnitude > 1_000_000 or (magnitude and magnitude < Decimal("0.000001")):
                    raise DomainError("unsupported_expression", "数字超出验证范围。")
                value = Fraction(exact)
            else:
                value = Fraction(node.value)
            if abs(value) > 1_000_000 or value.denominator > 1_000_000:
                raise DomainError("unsupported_expression", "数字超出验证范围。")
            return {0: value}
        if isinstance(node, ast.Name) and node.id == "x":
            if not allow_variable:
                raise DomainError("unsupported_expression", "参考答案须为不含变量的有理数算式，如2或-1/3。")
            return {1: Fraction(1)}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            sign = -1 if isinstance(node.op, ast.USub) else 1
            return {k: sign * v for k, v in visit(node.operand).items()}
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                sign = -1 if isinstance(node.op, ast.Sub) else 1
                return bounded_coefficients({
                    k: left.get(k, 0) + sign * right.get(k, 0) for k in left.keys() | right.keys()
                })
            if isinstance(node.op, ast.Mult):
                result = {}
                for a, av in left.items():
                    for b, bv in right.items():
                        if a + b > 4:
                            raise DomainError("unsupported_expression", "只验证四次及以下多项式。")
                        result[a + b] = result.get(a + b, 0) + av * bv
                return bounded_coefficients(result)
            if isinstance(node.op, ast.Div) and set(right) == {0} and right[0]:
                return bounded_coefficients({k: v / right[0] for k, v in left.items()})
            if isinstance(node.op, ast.Pow) and set(right) == {0} and right[0] in {0, 1, 2}:
                if right[0] == 0:
                    return {0: Fraction(1)}
                if right[0] == 1:
                    return left
                result = {}
                for a, av in left.items():
                    for b, bv in left.items():
                        if a + b > 4:
                            raise DomainError("unsupported_expression", "只验证四次及以下多项式。")
                        result[a + b] = result.get(a + b, 0) + av * bv
                return bounded_coefficients(result)
        raise DomainError(
            "unsupported_expression", "只验证变量 x 的多项式；不支持函数调用或变量分母。"
        )

    result = visit(tree.body)
    return {k: v for k, v in result.items() if v}


def check_answer(expression: str, proposed_answer: str, kind: str = "polynomial_identity") -> dict:
    """Evaluate both new and saved checks under the current bounded arithmetic rules."""
    try:
        if len(expression) > 300 or len(proposed_answer) > 300:
            raise DomainError("unsupported_expression", "两侧算式均应不超过 300 字符。")
        if kind == "polynomial_identity":
            equal = polynomial(expression) == polynomial(proposed_answer)
        elif kind == "linear_equation":
            if expression.count("=") != 1:
                raise DomainError("unsupported_expression", "方程必须包含恰好一个等号，乘法使用显式*。")
            left, right = (polynomial(side.strip()) for side in expression.split("="))
            coefficients = bounded_coefficients({
                k: left.get(k, Fraction(0)) - right.get(k, Fraction(0))
                for k in left.keys() | right.keys()
            })
            if any(k > 1 and v for k, v in coefficients.items()):
                raise DomainError("unsupported_expression", "仅验证化简后一元一次方程的唯一解。")
            a, b = coefficients.get(1, Fraction(0)), coefficients.get(0, Fraction(0))
            if not a:
                reason = "无解" if b else "有无穷多解"
                raise DomainError("unsupported_expression", f"该方程{reason}，不在唯一解验算范围内。")
            answer = polynomial(proposed_answer.strip(), allow_variable=False)
            solution = bounded_coefficients({0: -b / a})[0]
            equal = solution == answer.get(0, Fraction(0))
        else:
            raise DomainError("check_kind", "未知验算模式，不能推测保存记录的含义。")
        return {"status": "equivalent" if equal else "not_equivalent"}
    except DomainError as error:
        return {"status": "needs_review", "reason": error.message}

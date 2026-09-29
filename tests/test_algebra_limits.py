from fractions import Fraction

import pytest

from study_errorbook.algebra import check_answer, polynomial


@pytest.mark.parametrize('base', ['2', '1/2', '1000000', '0.000001'])
def test_nested_square_is_bounded_even_with_a_small_ast(base):
    expression = base
    for _ in range(12):
        expression = f'({expression})^2'
    result = check_answer(expression, expression)
    assert result['status'] == 'needs_review' and '4096' in result['reason']


def test_supported_large_intermediate_values_remain_exact():
    expression = '2'
    for _ in range(11):
        expression = f'({expression})^2'
    assert polynomial(expression) == {0: Fraction(2**2048)}
    assert polynomial(f'1/({expression})') == {0: Fraction(1, 2**2048)}
    assert check_answer(expression, f'{expression}+1')['status'] == 'not_equivalent'


@pytest.mark.parametrize('operation', ['add', 'subtract', 'multiply', 'divide', 'denominator'])
def test_each_arithmetic_path_rejects_oversized_coefficients(operation):
    # A compact expression at the 4096-bit boundary, within the existing AST limit.
    power = '65536'
    for _ in range(7):
        power = f'({power})^2'
    boundary = f'(({power})/2)^2*2'
    assert polynomial(boundary) == {0: Fraction(2**4095)}
    expression = {
        'add': f'({boundary})+({boundary})',
        'subtract': f'-({boundary})-({boundary})',
        'multiply': f'({boundary})*2',
        'divide': f'({boundary})/(1/2)',
        'denominator': f'(1/({boundary}))/2',
    }[operation]
    result = check_answer(expression, '0')
    assert result['status'] == 'needs_review' and '4096' in result['reason']

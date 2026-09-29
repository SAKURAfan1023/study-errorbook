import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_check_result_declares_modes_statuses_and_preserves_wire_results(tmp_path):
    env = {**os.environ, 'STUDY_DATA_DIR': str(tmp_path/'data'),
           'STUDY_INPUT_ROOT': str(tmp_path/'inbox'), 'STUDY_CONFIG_FILE': str(tmp_path/'absent.env'),
           'TEXTIN_APP_ID': '', 'TEXTIN_SECRET_CODE': ''}
    async with stdio_client(StdioServerParameters(
        command=sys.executable, args=['-m', 'study_errorbook.server'], env=env,
    )) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            check = next(t for t in (await session.list_tools()).tools if t.name == 'check_exercise')
            schema = check.outputSchema
            assert set(schema['properties']['status']['enum']) == {'equivalent', 'not_equivalent', 'needs_review'}
            assert set(schema['properties']['kind']['enum']) == {'polynomial_identity', 'linear_equation'}
            assert {'id', 'kind', 'status', 'expression', 'proposed_answer', 'scope'} <= set(schema['required'])
            assert 'reason' in schema['properties'] and 'reason' not in schema['required']
            assert all(schema['properties'][field]['description'] for field in ('id', 'status', 'scope', 'reason'))
            for expression, answer, kind, expected in (
                ('2*(x+3)', '2*x+6', 'polynomial_identity', 'equivalent'),
                ('2*(x+3)', '2*x+3', 'polynomial_identity', 'not_equivalent'),
                ('1/x', '1', 'polynomial_identity', 'needs_review'),
                ('2*x=4', '2', 'linear_equation', 'equivalent'),
                ('2*x=4', '3', 'linear_equation', 'not_equivalent'),
                ('x=x', '2', 'linear_equation', 'needs_review'),
            ):
                result = await session.call_tool('check_exercise', {
                    'expression': expression, 'proposed_answer': answer, 'kind': kind})
                assert not result.isError
                data = result.structuredContent
                assert data['status'] == expected and data['kind'] == kind
                assert data['expression'] == expression and data['proposed_answer'] == answer
                assert data['id'].startswith('check_') and data['scope']
                if expected == 'needs_review':
                    assert data['reason']
                else:
                    assert 'reason' not in data
                assert json.loads(result.content[0].text) == data

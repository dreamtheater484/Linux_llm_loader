"""Exercise the actual patched Tabby parser when the optional engine is installed."""
import json
import subprocess

import pytest

from loader.engines import EXL_PYTHON, TABBY


def test_installed_qwen_parser_uses_declared_types():
    if not EXL_PYTHON.is_file() or not TABBY.is_dir():
        pytest.skip('Optional Tabby runtime is not installed')
    probe = r'''
import json
from endpoints.OAI.utils.tools import parse_toolcalls
from endpoints.OAI.types.tools import ToolSpec

def parse(raw, kind):
    tools = [ToolSpec.model_validate({'type':'function','function':{
        'name':'probe','description':'Typed mock','parameters':{
            'type':'object','properties':{'value':{'type':kind}}}}})]
    text = '<tool_call><function=probe><parameter=value>' + raw + '</parameter></function></tool_call>'
    calls = parse_toolcalls(text, 'qwen3_coder', tools=tools)
    assert len(calls) == 1
    return json.loads(calls[0].function.arguments)['value']

for raw in ['123', '00123', 'true', 'False', 'null', '1.000', '"quoted"', 'a\nb', '{"key":1}']:
    assert parse(raw, 'string') == raw
for raw, expected in [('true',True),('True',True),('false',False),('False',False)]:
    assert parse(raw, 'boolean') is expected
assert parse('7','integer') == 7
assert parse('[2,4]','array') == [2,4]
assert parse('{"count":7}','object') == {'count':7}
# Do not evaluate Python expressions or make broad guesses about invalid values.
assert parse('__import__("os").system("never execute")','boolean').startswith('__import__')
assert parse('yes','boolean') == 'yes'
duplicate = '<tool_call><function=probe><parameter=value>1</parameter><parameter=value>2</parameter></function></tool_call>'
assert parse_toolcalls(duplicate, 'qwen3_coder') == []
print(json.dumps({'passed': True}))
'''
    result = subprocess.run([str(EXL_PYTHON), '-c', probe], cwd=TABBY,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout.splitlines()[-1]) == {'passed': True}

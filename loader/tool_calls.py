"""Validate tool contracts; syntax parsing belongs to the model's engine parser."""
import json
from pathlib import Path
from typing import Literal

from jsonschema import ValidationError as SchemaValidationError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field
from referencing import Registry

from .reasoning import native_template


def native_tool_format(model):
    # Qualify both architecture and the selected template. Never guess a parser
    # from a display name, or turn arbitrary tool-looking prose into calls.
    if model.get('architecture') == 'Qwen4ExpForConditionalGeneration':
        template = native_template(Path(model['path']), exl3=True)
        if all(tag in template for tag in ('<tool_call>', '<function=', '<parameter=')):
            return 'qwen3_coder'
    return None


class FunctionSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    description: str = ''
    parameters: dict = Field(default_factory=lambda: {'type': 'object', 'properties': {}})
    strict: bool | None = None


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['function']
    function: FunctionSpec


class NamedFunction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str


class NamedToolChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['function']
    function: NamedFunction


class ToolRequest(BaseModel):
    tools: list[ToolSpec] | None = Field(None, max_length=128)
    tool_choice: Literal['auto', 'none', 'required'] | NamedToolChoice | None = None
    parallel_tool_calls: bool = True

    def tool_payload(self, engine, tool_format):
        if self.tool_choice == 'required' or isinstance(self.tool_choice, NamedToolChoice):
            raise ValueError('This serving stack cannot enforce required or named tool_choice. Use auto or none.')
        if self.tools and any(t.function.strict for t in self.tools):
            raise ValueError('strict tool generation is unsupported. Arguments are validated after generation; omit strict or use false.')
        mode = self.tool_choice or ('auto' if self.tools else 'none')
        # Disable parsing when no functions were offered, even if the model's
        # native parser is enabled globally (ordinary chat may quote tool tags).
        if mode == 'none' or not self.tools:
            return {'tool_choice': 'none'}
        if engine not in ('exl3', 'gguf') or not tool_format:
            raise ValueError('Structured tool calling is not configured for this model and engine.')
        if not self.parallel_tool_calls:
            raise ValueError('parallel_tool_calls=false is unsupported by this serving stack; omit it or use true.')
        tools = [t.model_dump(exclude_none=True) for t in self.tools]
        names = [t['function']['name'] for t in tools]
        if len(names) != len(set(names)):
            raise ValueError('Tool function names must be unique.')
        for tool in tools:
            tool['function'].pop('strict', None)
            schema = tool['function']['parameters']
            try:
                validator_for(schema).check_schema(schema)
            except SchemaValidationError:
                raise ValueError('A tool parameters schema is invalid.') from None
            except Exception:
                raise ValueError('A tool parameters schema is invalid or unsupported.') from None
        return {'tools': tools, 'tool_choice': 'auto', 'parallel_tool_calls': True}


def arguments_object(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('Duplicate JSON argument key.')
            result[key] = item
        return result

    def nonfinite(_):
        raise ValueError('Non-finite JSON argument.')

    if not isinstance(value, str):
        raise ValueError('Tool arguments must be a JSON string.')
    result = json.loads(value, object_pairs_hook=unique, parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError('Tool arguments must encode a JSON object.')
    return result


def validate_tool_history(messages):
    pending, seen = set(), set()
    for message in messages:
        calls = message.get('tool_calls')
        if calls:
            if message.get('role') != 'assistant' or not isinstance(calls, list) or pending:
                raise ValueError('Tool calls must belong to an assistant turn after previous tool results.')
            for call in calls:
                if not isinstance(call, dict) or call.get('type') != 'function':
                    raise ValueError('History contains an invalid function call.')
                identity, function = call.get('id'), call.get('function')
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise ValueError('History tool call IDs must be nonempty and unique.')
                if not isinstance(function, dict) or not isinstance(function.get('name'), str) or not function['name']:
                    raise ValueError('History contains an invalid function name.')
                arguments_object(function.get('arguments'))
                pending.add(identity)
                seen.add(identity)
        elif message.get('role') == 'tool':
            identity = message.get('tool_call_id')
            if not isinstance(identity, str) or identity not in pending:
                raise ValueError('A tool result must reference a pending assistant tool_call_id.')
            pending.remove(identity)
        elif pending:
            raise ValueError('Provide results for all assistant tool calls before continuing the conversation.')
    if pending:
        raise ValueError('Provide results for all assistant tool calls before requesting an answer.')


class ToolResponseError(ValueError):
    """The engine returned an incomplete or invalid structured call."""


class ToolCallAccumulator:
    def __init__(self):
        self.calls = {}

    def add(self, deltas):
        if not isinstance(deltas, list):
            raise ToolResponseError('Engine tool_calls must be an array.')
        for delta in deltas:
            if not isinstance(delta, dict):
                raise ToolResponseError('Engine returned an invalid tool call delta.')
            index = delta.get('index')
            if type(index) is not int or not 0 <= index < 128:
                raise ToolResponseError('Engine tool call delta is missing a valid index.')
            call = self.calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
            if delta.get('type', 'function') != 'function':
                raise ToolResponseError('Engine returned an unsupported tool type.')
            if delta.get('id') is not None:
                identity = delta['id']
                if not isinstance(identity, str) or not identity or (call['id'] and call['id'] != identity):
                    raise ToolResponseError('Engine returned inconsistent tool call IDs.')
                call['id'] = identity
            function = delta.get('function', {})
            if not isinstance(function, dict):
                raise ToolResponseError('Engine returned an invalid tool function.')
            for key in ('name', 'arguments'):
                fragment = function.get(key, '')
                if not isinstance(fragment, str):
                    raise ToolResponseError('Engine returned a non-string tool function fragment.')
                call['function'][key] += fragment

    def finish(self, payload, finish_reason):
        if not self.calls:
            if finish_reason == 'tool_calls':
                raise ToolResponseError('Engine reported tool_calls but returned no parsed calls.')
            return []
        if payload.get('tool_choice') != 'auto' or finish_reason != 'tool_calls':
            raise ToolResponseError('Engine returned unrequested or incomplete tool calls.')
        if sorted(self.calls) != list(range(len(self.calls))):
            raise ToolResponseError('Engine tool call indexes must be consecutive.')
        offered = {t['function']['name']: t['function']['parameters'] for t in payload.get('tools', [])}
        result, seen = [], set()
        for index in sorted(self.calls):
            call = self.calls[index]
            identity, function = call['id'], call['function']
            if not identity or identity in seen or function['name'] not in offered:
                raise ToolResponseError('Engine returned an unknown function or invalid tool call ID.')
            try:
                arguments = arguments_object(function['arguments'])
                schema = offered[function['name']]
                # An empty registry forbids fetching schemas from external URLs.
                validator_for(schema)(schema, registry=Registry()).validate(arguments)
            except Exception:
                raise ToolResponseError('Engine tool arguments do not match the offered JSON schema.') from None
            seen.add(identity)
            result.append(call)
        return result

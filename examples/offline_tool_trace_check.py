#!/usr/bin/env python3
"""Verify a real Agent -> Generation -> Tool -> Generation trace offline.

Run inside the existing LiteLLM image so the Langfuse SDK is already available.
The gateway URL must point to the actual Nginx entry, not LiteLLM directly.
Credentials are read from the container environment and never printed.
"""

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from langfuse import Langfuse, propagate_attributes


def required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise SystemExit('Missing required environment variable: ' + name)
    return value


def opener_for(cafile, ca_pem):
    context = ssl.create_default_context(cafile=cafile or None)
    if ca_pem:
        context.load_verify_locations(cadata=ca_pem)
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )


def chat(opener, base, key, model, messages, trace_id, parent_id, session, tools=None):
    body = {
        'model': model,
        'messages': messages,
        'max_tokens': 256,
        'temperature': 0,
        'metadata': {'session_id': session, 'trace_user_id': 'offline-validation',
                     'trace_name': 'offline-tool-chain-validation'},
    }
    if tools:
        body['tools'] = tools
        body['tool_choice'] = {'type': 'function', 'function': {'name': 'add_numbers'}}
    request = urllib.request.Request(
        base + '/v1/chat/completions', data=json.dumps(body).encode('utf-8'), method='POST',
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                 'traceparent': '00-' + trace_id + '-' + parent_id + '-01',
                 'x-session-id': session},
    )
    with opener.open(request, timeout=300) as response:
        if response.status != 200:
            raise RuntimeError('Gateway returned HTTP ' + str(response.status))
        return json.load(response)


def observations(opener, host, public_key, secret_key, trace_id):
    token = base64.b64encode((public_key + ':' + secret_key).encode('utf-8')).decode('ascii')
    query = urllib.parse.urlencode({'traceId': trace_id, 'limit': 100,
                                   'fields': 'core,basic,model,usage,io'})
    request = urllib.request.Request(
        host + '/api/public/v2/observations?' + query,
        headers={'Authorization': 'Basic ' + token},
    )
    with opener.open(request, timeout=20) as response:
        result = json.load(response)
    rows = result.get('data') if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError('Unexpected Langfuse observations response')
    return rows


def verify_tree(rows, trace_id, agent_id):
    rows = list({r['id']: r for r in rows if r.get('traceId') == trace_id and r.get('id')}.values())
    agents = [r for r in rows if r.get('id') == agent_id and r.get('type') == 'AGENT']
    generations = [r for r in rows if r.get('type') == 'GENERATION'
                   and r.get('parentObservationId') == agent_id]
    tools = [r for r in rows if r.get('type') == 'TOOL' and r.get('name') == 'add_numbers'
             and r.get('parentObservationId') == agent_id]
    complete = [r for r in generations if r.get('model') and r.get('input') is not None
                and r.get('output') is not None and isinstance(r.get('usageDetails'), dict)
                and isinstance(r['usageDetails'].get('total'), (int, float))
                and r['usageDetails']['total'] > 0 and r.get('startTime') and r.get('endTime')]
    return bool(agents and len(complete) >= 2 and tools), {
        'agent': len(agents), 'generation': len(generations),
        'generation_complete': len(complete), 'tool': len(tools),
    }


def main():
    gateway = required('GATEWAY_URL').rstrip('/')
    host = required('LANGFUSE_HOST').rstrip('/')
    for value, name in ((gateway, 'GATEWAY_URL'), (host, 'LANGFUSE_HOST')):
        parts = urllib.parse.urlsplit(value)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.path not in ('', '/')
                or parts.query or parts.fragment or parts.username or parts.password):
            raise SystemExit(name + ' must be an HTTP(S) origin without query or fragment')
    key = required('LITELLM_MASTER_KEY')
    public_key = required('LANGFUSE_PUBLIC_KEY')
    secret_key = required('LANGFUSE_SECRET_KEY')
    model = os.environ.get('GATEWAY_MODEL', 'qwen-strata')
    opener = opener_for(os.environ.get('GATEWAY_CA_FILE'), os.environ.get('GATEWAY_CA_PEM'))
    session = 'offline-validation-' + uuid.uuid4().hex[:12]
    client = Langfuse(public_key=public_key, secret_key=secret_key, base_url=host)
    tools = [{'type': 'function', 'function': {'name': 'add_numbers',
              'description': 'Add two integer numbers.',
              'parameters': {'type': 'object', 'properties': {
                  'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
                  'required': ['a', 'b'], 'additionalProperties': False}}}]
    messages = [
        {'role': 'system', 'content': 'Use add_numbers to calculate. After the tool result, answer with the result only. Do not think.'},
        {'role': 'user', 'content': 'Calculate 17 + 25 using add_numbers.'},
    ]
    with propagate_attributes(session_id=session, user_id='offline-validation',
                              trace_name='offline-tool-chain-validation',
                              tags=['offline-validation'], environment='offline-validation'):
        with client.start_as_current_observation(name='offline-tool-chain-validation', as_type='agent',
                                                 input=messages, metadata={'validation': True}) as agent:
            trace_id = client.get_current_trace_id()
            agent_id = client.get_current_observation_id()
            first = chat(opener, gateway, key, model, messages, trace_id, agent_id, session, tools)
            choices = first.get('choices') or []
            message = choices[0].get('message', {}) if choices else {}
            calls = message.get('tool_calls') or []
            if len(calls) != 1 or calls[0].get('function', {}).get('name') != 'add_numbers':
                raise RuntimeError('Model did not return exactly one add_numbers tool call')
            call = calls[0]
            args = json.loads(call['function']['arguments'])
            if type(args.get('a')) is not int or type(args.get('b')) is not int:
                raise RuntimeError('Tool arguments are not integers')
            messages.append(message)
            with client.start_as_current_observation(name='add_numbers', as_type='tool', input=args,
                                                     metadata={'execution_source': 'client'}) as tool:
                result = args['a'] + args['b']
                tool.update(output={'result': result})
            if result != 42:
                raise RuntimeError('Tool result did not match expected value')
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps({'result': result})})
            second = chat(opener, gateway, key, model, messages, trace_id, agent_id, session)
            choices = second.get('choices') or []
            answer = (choices[0].get('message') or {}).get('content') if choices else None
            if not answer or '42' not in answer:
                raise RuntimeError('Final model answer did not contain 42')
            agent.update(output={'answer': answer})
    client.flush()
    print(json.dumps({'step': 'requests_complete', 'trace_id': trace_id, 'session_id': session,
                      'tool_result': result, 'answer': answer}), flush=True)
    deadline = time.monotonic() + 180
    last_counts = {}
    while time.monotonic() < deadline:
        try:
            rows = observations(opener, host, public_key, secret_key, trace_id)
            ok, last_counts = verify_tree(rows, trace_id, agent_id)
            if ok:
                print(json.dumps({'step': 'trace_verified', 'trace_id': trace_id,
                                  'agent_id': agent_id, 'nodes': last_counts}), flush=True)
                return
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 429, 500, 502, 503, 504):
                raise RuntimeError('Langfuse observations API HTTP ' + str(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(5)
    raise RuntimeError('Trace tree was not complete before timeout; node counts=' + json.dumps(last_counts))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('VALIDATION_FAILED:', type(exc).__name__, str(exc), file=sys.stderr)
        sys.exit(1)

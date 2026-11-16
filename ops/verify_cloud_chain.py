"""Run on the cloud host after Strata reports loaded=true; do not print keys."""
import json, secrets, ssl, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

def utc():
    return datetime.now(timezone.utc).isoformat()

def get_key():
    result = subprocess.run(['docker', 'exec', 'llm-a100-litellm-1', 'printenv', 'LITELLM_MASTER_KEY'],
                            capture_output=True, text=True, check=True)
    key = result.stdout.strip()
    if not key:
        raise RuntimeError('LiteLLM master key is not set')
    return key

def send(stream, key):
    trace_id, parent_id = secrets.token_hex(16), secrets.token_hex(8)
    payload = {'model': 'qwen-strata', 'messages': [{'role': 'user', 'content': 'What is 17 plus 25? Answer with just the number.'}],
               'max_tokens': 128, 'stream': stream}
    if stream:
        payload['stream_options'] = {'include_usage': True}
    request = urllib.request.Request('https://127.0.0.1:8800/v1/chat/completions',
        data=json.dumps(payload).encode(), method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key,
            'traceparent': '00-' + trace_id + '-' + parent_id + '-01'})
    # The loopback endpoint uses this deployment's self-signed certificate.
    context = ssl._create_unverified_context()
    started = time.monotonic()
    result = {'utc': utc(), 'trace_id': trace_id, 'stream': stream}
    try:
        with urllib.request.urlopen(request, context=context, timeout=600) as response:
            result['status'] = response.status
            if not stream:
                data = json.load(response)
                choices = data.get('choices') or []
                result.update(model=data.get('model'), usage=data.get('usage'),
                              content=(choices[0].get('message') or {}).get('content') if choices else None,
                              finish_reason=choices[0].get('finish_reason') if choices else None)
            else:
                frames, done, usage, content, reasoning = 0, False, None, [], False
                for line in response:
                    if not line.startswith(b'data:'): continue
                    raw = line[5:].strip()
                    if raw == b'[DONE]': done = True; break
                    if not raw: continue
                    obj = json.loads(raw); frames += 1
                    usage = obj.get('usage') or usage
                    for choice in obj.get('choices') or []:
                        delta = choice.get('delta') or {}
                        if delta.get('content'): content.append(delta['content'])
                        reasoning |= bool(delta.get('reasoning_content') or delta.get('reasoning'))
                result.update(frames=frames, done=done, usage=usage,
                              content=''.join(content), reasoning_delta=reasoning)
    except urllib.error.HTTPError as exc:
        result.update(status=exc.code, error=exc.read(1024).decode('utf-8', 'replace'))
    except Exception as exc:
        result.update(error=type(exc).__name__ + ': ' + str(exc))
    result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    print('REQUEST', json.dumps(result, ensure_ascii=False), flush=True)
    return result

def lookup(trace_id):
    sql = ("SELECT project_id, trace_id, name, type, provided_model_name, "
           "provided_usage_details, usage_details, toString(start_time) AS start_time "
           "FROM default.events_full WHERE trace_id = '" + trace_id + "' "
           "ORDER BY start_time DESC FORMAT JSONEachRow")
    result = subprocess.run(['docker', 'exec', 'llm-a100-clickhouse-1', 'sh', '-c',
        'exec clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "$1"',
        'verify', sql], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('ClickHouse query failed: ' + result.stderr[:1000])
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

key = get_key()
results = [send(False, key), send(True, key)]
del key
pending = {r['trace_id'] for r in results}
deadline = time.monotonic() + 240
while pending and time.monotonic() < deadline:
    for trace_id in tuple(pending):
        rows = lookup(trace_id)
        if rows:
            print('LANGFUSE_EVENT', json.dumps(rows, ensure_ascii=False), flush=True)
            pending.remove(trace_id)
    if pending: time.sleep(5)
if pending:
    print('TRACE_NOT_FOUND', ','.join(sorted(pending)), flush=True)
if pending or any(r.get('status') != 200 for r in results):
    sys.exit(1)
if not results[0].get('content') or not results[1].get('done') or not results[1].get('content'):
    sys.exit(2)
print('REQUESTS_AND_TRACES_FOUND', flush=True)

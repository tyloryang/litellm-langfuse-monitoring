#!/usr/bin/env python3
"""Measure a synthetic near-limit chat through LiteLLM using vLLM token counts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tokenizer-url', required=True, help='vLLM origin, without /v1')
    parser.add_argument('--gateway-url', required=True, help='LiteLLM base URL ending in /v1')
    parser.add_argument('--upstream-model', default='qwen3.6-27b')
    parser.add_argument('--model', default='qwen-vllm')
    parser.add_argument('--target-input-tokens', type=int, default=262080)
    parser.add_argument('--max-output-tokens', type=int, default=64)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--output', default='context-probe-result.json')
    args = parser.parse_args()
    if args.target_input_tokens < 1024 or args.max_output_tokens < 1:
        parser.error('Use at least 1024 input tokens and one output token')
    keys = [os.environ.get(name) for name in ('VLLM_API_KEY', 'LITELLM_API_KEY')]
    if not all(keys):
        parser.error('Set VLLM_API_KEY and LITELLM_API_KEY in the environment')
    # Local/private service addresses must not accidentally go through a system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    run_id = 'vllm-context-' + time.strftime('%Y%m%d-%H%M%S')
    report = {'run_id': run_id, 'model': args.model,
              'requested_input_tokens': args.target_input_tokens,
              'max_output_tokens': args.max_output_tokens,
              'capacity_pass': False, 'retrieval_pass': False}

    def post(url, key, payload):
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                     headers={'Authorization': 'Bearer ' + key,
                                              'Content-Type': 'application/json'})
        with opener.open(req, timeout=args.timeout) as response:
            return json.load(response)

    rng = random.Random(271828)
    markers = ['AMBER-' + str(rng.randrange(100000, 999999)),
               'COBALT-' + str(rng.randrange(100000, 999999)),
               'JADE-' + str(rng.randrange(100000, 999999))]
    rows = ['Record %06d: value %010d; category %s.\n' %
            (i, rng.randrange(10**9, 10**10), rng.choice(('oak', 'pine', 'elm', 'ash')))
            for i in range(args.target_input_tokens // 4)]

    def messages(n, padding=0):
        body = ('This is a synthetic context capacity test. Remember the three MARKER values.\n'
                + 'MARKER_A=' + markers[0] + '\n'
                + ''.join(rows[:n//2]) + 'MARKER_B=' + markers[1] + '\n'
                + ''.join(rows[n//2:n]) + ' x' * padding + '\n'
                + 'MARKER_C=' + markers[2] + '\n'
                + 'Reply with only the values of MARKER_A, MARKER_B, MARKER_C in order, separated by commas.')
        return [{'role': 'user', 'content': body}]

    def count(msgs):
        result = post(args.tokenizer_url.rstrip('/') + '/tokenize', keys[0],
                      {'model': args.upstream_model, 'messages': msgs,
                       'chat_template_kwargs': {'enable_thinking': False}})
        report['server_max_model_len'] = result['max_model_len']
        return int(result['count'])

    start = time.monotonic()
    try:
        count(messages(0))
        if args.target_input_tokens + args.max_output_tokens > report['server_max_model_len']:
            raise ValueError('Requested input plus output exceeds the active server context limit')
        # Find the largest whole-record input below the requested token count.
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if count(messages(mid)) <= args.target_input_tokens:
                lo = mid
            else:
                hi = mid - 1
        n = lo
        base_count = count(messages(n))
        lo, hi = 0, max(64, 2 * (args.target_input_tokens - base_count) + 32)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if count(messages(n, mid)) <= args.target_input_tokens:
                lo = mid
            else:
                hi = mid - 1
        msgs = messages(n, lo)
        measured = count(msgs)
        if not args.target_input_tokens - 32 <= measured <= args.target_input_tokens:
            raise ValueError('Could not build an input within 32 tokens of the target')
        report.update({'tokenizer_prompt_tokens': measured,
                       'prompt_sha256': hashlib.sha256(msgs[0]['content'].encode()).hexdigest(),
                       'preparation_seconds': round(time.monotonic() - start, 2)})
        print(json.dumps({'phase': 'requesting', 'run_id': run_id, 'prompt_tokens': measured,
                          'output_budget': args.max_output_tokens}), flush=True)
        payload = {'model': args.model, 'messages': msgs, 'temperature': 0,
                   'max_tokens': args.max_output_tokens,
                   'chat_template_kwargs': {'enable_thinking': False},
                   'metadata': {'trace_name': run_id, 'session_id': run_id}}
        report['request_bytes'] = len(json.dumps(payload).encode())
        inference_start = time.monotonic()
        result = post(args.gateway_url.rstrip('/') + '/chat/completions', keys[1], payload)
        report['inference_seconds'] = round(time.monotonic() - inference_start, 2)
        usage = result.get('usage') or {}
        choices = result.get('choices') or []
        choice = choices[0] if choices else {}
        content = (choice.get('message') or {}).get('content') or ''
        report.update({'usage': usage, 'finish_reason': choice.get('finish_reason'),
                       'response_model': result.get('model'), 'response_id': result.get('id'),
                       'answer': content, 'expected_markers': markers})
        actual_total = usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)
        report['actual_total_tokens'] = actual_total
        report['reached_server_context_limit'] = actual_total == report['server_max_model_len']
        # Capacity and retrieval are separate: a correct HTTP response alone is not enough.
        report['capacity_pass'] = (usage.get('prompt_tokens') == measured
                                   and usage.get('completion_tokens', 0) > 0
                                   and choice.get('finish_reason') in ('stop', 'length'))
        positions = [content.find(marker) for marker in markers]
        report['retrieval_pass'] = all(x >= 0 for x in positions) and positions == sorted(positions)
    except urllib.error.HTTPError as exc:
        report['http_status'] = exc.code
        # Do not persist upstream errors that might include request headers or credentials.
        report['error_type'] = 'HTTPError'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        if isinstance(exc, ValueError):
            report['error'] = str(exc)
    finally:
        report['total_seconds'] = round(time.monotonic() - start, 2)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True), flush=True)
    return 0 if report['capacity_pass'] else 1


if __name__ == '__main__':
    sys.exit(main())

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


def load_checker():
    langfuse = types.ModuleType('langfuse')
    langfuse.Langfuse = object
    langfuse.propagate_attributes = object
    path = Path(__file__).resolve().parents[1] / 'examples/offline_tool_trace_check.py'
    spec = importlib.util.spec_from_file_location('offline_tool_trace_check', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'langfuse': langfuse}):
        spec.loader.exec_module(module)
    return module


checker = load_checker()


def sample_rows():
    return [
        {'traceId': 'trace', 'id': 'agent', 'type': 'AGENT'},
        {'traceId': 'trace', 'id': 'tool', 'type': 'TOOL', 'name': 'add_numbers',
         'parentObservationId': 'agent'},
        *[{'traceId': 'trace', 'id': str(i), 'type': 'GENERATION',
           'parentObservationId': 'agent', 'model': 'test-model', 'input': {'a': 1},
           'output': {'result': 42}, 'usageDetails': {'total': 20},
           'startTime': 'start', 'endTime': 'end'} for i in (1, 2)],
    ]


class TraceAcceptanceTest(unittest.TestCase):
    def test_complete_tree_passes(self):
        ok, counts = checker.verify_tree(sample_rows(), 'trace', 'agent')
        self.assertTrue(ok)
        self.assertEqual(counts['generation_complete'], 2)

    def test_wrong_parent_or_incomplete_fields_fail(self):
        for field, value in [('parentObservationId', 'other'), ('model', ''),
                             ('input', None), ('output', None),
                             ('usageDetails', {}), ('endTime', None)]:
            rows = sample_rows()
            rows[-1][field] = value
            with self.subTest(field=field):
                self.assertFalse(checker.verify_tree(rows, 'trace', 'agent')[0])

    def test_duplicate_generation_and_foreign_trace_fail(self):
        rows = sample_rows()
        rows[-1]['id'] = rows[-2]['id']
        self.assertFalse(checker.verify_tree(rows, 'trace', 'agent')[0])
        rows = sample_rows()
        rows[-1]['traceId'] = 'different'
        self.assertFalse(checker.verify_tree(rows, 'trace', 'agent')[0])

    def test_observation_query_requests_model_usage_and_io(self):
        class Response:
            def __enter__(self):
                import io
                return io.BytesIO(json.dumps({'data': []}).encode())
            def __exit__(self, *args):
                return False

        class Opener:
            def open(self, request, timeout):
                fields = parse_qs(urlsplit(request.full_url).query)['fields'][0].split(',')
                assert {'model', 'usage', 'io'}.issubset(fields)
                assert request.get_header('Authorization').startswith('Basic ')
                return Response()

        self.assertEqual(checker.observations(Opener(), 'http://example.invalid', 'pk', 'sk', 'trace'), [])


if __name__ == '__main__':
    unittest.main()

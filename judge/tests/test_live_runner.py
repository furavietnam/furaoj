from django.test import TestCase
from judge.models import Judge, Language, Problem, ProblemGroup
from judge.utils.live_runner import execute_live_run


class LiveRunnerTestCase(TestCase):
    def setUp(self):
        self.group = ProblemGroup.objects.create(name='runner-group', full_name='Runner Group')
        self.problem = Problem.objects.create(
            code='testrunner',
            name='Test Runner',
            time_limit=1.0,
            memory_limit=65536,
            points=100,
            group=self.group,
        )
        self.lang_cpp, _ = Language.objects.get_or_create(key='CPP20', defaults={'name': 'C++20'})
        self.lang_py, _ = Language.objects.get_or_create(key='PY3', defaults={'name': 'Python 3'})
        self.judge = Judge.objects.create(name='furaoj', online=True, auth_key='runnerjudge')
        self.judge.runtimes.add(self.lang_cpp, self.lang_py)

    def test_live_runner_cpp_ac(self):
        source = '#include <iostream>\nint main() { int a, b; if (std::cin >> a >> b) std::cout << (a + b) << std::endl; return 0; }'
        testcases = [{'id': 1, 'input': '10 20\n', 'expected': '30'}]
        res = execute_live_run(self.problem, 'CPP20', source, testcases)
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(len(res['results']), 1)
        self.assertEqual(res['results'][0]['status'], 'AC')
        self.assertEqual(res['results'][0]['output'].strip(), '30')

    def test_live_runner_cpp_wa(self):
        source = '#include <iostream>\nint main() { std::cout << 42 << std::endl; return 0; }'
        testcases = [{'id': 1, 'input': '', 'expected': '100'}]
        res = execute_live_run(self.problem, 'CPP20', source, testcases)
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(res['results'][0]['status'], 'WA')

    def test_live_runner_compile_error(self):
        source = 'int main() { not valid c++ syntax! }'
        testcases = [{'id': 1, 'input': '', 'expected': ''}]
        res = execute_live_run(self.problem, 'CPP20', source, testcases)
        self.assertEqual(res['status'], 'CE')
        self.assertIsNotNone(res['compile_error'])

    def test_live_runner_tle(self):
        source = '#include <iostream>\nint main() { while(true); return 0; }'
        testcases = [{'id': 1, 'input': '', 'expected': ''}]
        res = execute_live_run(self.problem, 'CPP20', source, testcases)
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(res['results'][0]['status'], 'TLE')

    def test_live_runner_oversized_payload(self):
        source = 'a' * (1024 * 1024 + 10)
        res = execute_live_run(self.problem, 'CPP20', source, [])
        self.assertEqual(res['status'], 'ERROR')

    def test_live_runner_py3_output(self):
        source = 'print("Hello Python Live")'
        testcases = [{'id': 1, 'input': '', 'expected': 'Hello Python Live'}]
        res = execute_live_run(self.problem, 'PY3', source, testcases)
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(len(res['results']), 1)
        r = res['results'][0]
        self.assertEqual(r['status'], 'AC')
        self.assertEqual(r['output'].strip(), 'Hello Python Live')
        self.assertEqual(r['actual'].strip(), 'Hello Python Live')
        self.assertGreaterEqual(r['time'], 0.0)
        self.assertGreaterEqual(r['execution_time'], 0.0)

    def test_live_runner_py3_rte_feedback(self):
        source = '1 / 0'
        testcases = [{'id': 1, 'input': '', 'expected': ''}]
        res = execute_live_run(self.problem, 'PY3', source, testcases)
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(len(res['results']), 1)
        r = res['results'][0]
        self.assertEqual(r['status'], 'RTE')
        self.assertIn('ZeroDivisionError', r['feedback'])
        self.assertIn('division by zero', r['stderr'])

    def test_live_runner_py3_empty_output(self):
        source = 'x = 42'
        testcases = [{'id': 1, 'input': '', 'expected': ''}]
        res = execute_live_run(self.problem, 'PY3', source, testcases)
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(len(res['results']), 1)
        r = res['results'][0]
        self.assertEqual(r['status'], 'EXECUTED')
        self.assertEqual(r['output'], '')
        self.assertEqual(r['actual'], '')


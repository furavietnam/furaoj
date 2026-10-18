from django.test import TestCase
from judge.models import Problem, ProblemGroup
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

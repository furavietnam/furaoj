import json
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from judge.models import Language, Problem, ProblemData, ProblemDraft, ProblemGroup, Profile
from judge.utils.live_runner import execute_live_run


class LiveIdeStressTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='stressuser', password='password123')
        self.profile = Profile.objects.create(user=self.user)
        self.group = ProblemGroup.objects.create(name='stress-group', full_name='Stress Group')
        self.problem = Problem.objects.create(
            code='stressprob',
            name='Stress Problem',
            time_limit=1.0,
            memory_limit=65536,
            points=100,
            group=self.group,
            is_public=True,
        )
        self.lang_cpp, _ = Language.objects.get_or_create(key='CPP20', defaults={'name': 'C++20'})
        self.lang_py, _ = Language.objects.get_or_create(key='PY3', defaults={'name': 'Python 3'})
        self.problem.allowed_languages.add(self.lang_cpp, self.lang_py)
        self.client.login(username='stressuser', password='password123')

    def test_payload_too_large_rejected(self):
        url = reverse('problem_live_run', args=[self.problem.code])
        oversized_data = 'x' * (1024 * 1024 + 100)
        res = self.client.post(url, oversized_data, content_type='application/json')
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data.get('error'), 'payload_too_large')

    def test_too_many_testcases_rejected(self):
        url = reverse('problem_live_run', args=[self.problem.code])
        payload = {
            'language': self.lang_cpp.id,
            'source': 'int main() { return 0; }',
            'testcases': [{'id': i, 'input': f'{i}', 'expected': f'{i}'} for i in range(1, 7)],
        }
        res = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data.get('error'), 'too_many_testcases')

    def test_malformed_json_and_injection(self):
        url = reverse('problem_live_run', args=[self.problem.code])
        res = self.client.post(url, '{ "source": "int main()", unclosed json', content_type='application/json')
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data.get('error'), 'invalid_json')

    def test_line_ending_normalization(self):
        # Code outputs Windows CRLF, expected has Unix LF
        source_crlf = '#include <iostream>\nint main() { std::cout << "line1\\r\\nline2\\r\\n"; return 0; }'
        res = execute_live_run(self.problem, 'CPP20', source_crlf, [
            {'id': 1, 'input': '', 'expected': 'line1\nline2\n'}
        ])
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(res['results'][0]['status'], 'AC')

    def test_output_limit_exceeded_truncation(self):
        # Code produces large volume of text (> 64KB)
        source = '#include <iostream>\nint main() { for (int i = 0; i < 20000; ++i) std::cout << "0123456789\\n"; return 0; }'
        res = execute_live_run(self.problem, 'CPP20', source, [
            {'id': 1, 'input': '', 'expected': 'hello'}
        ])
        self.assertEqual(res['status'], 'OK')
        # Should be OLE or WA, and output truncated without crashing runner
        self.assertIn(res['results'][0]['status'], ['OLE', 'WA'])
        self.assertLessEqual(len(res['results'][0]['output']), 65536 + 100)

    def test_tle_process_kill(self):
        # Infinite loop terminates cleanly
        source = '#include <iostream>\nint main() { while (1) {} return 0; }'
        res = execute_live_run(self.problem, 'CPP20', source, [
            {'id': 1, 'input': '', 'expected': 'something'}
        ])
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(res['results'][0]['status'], 'TLE')

    def test_file_io_mismatch_behavior(self):
        file_prob = Problem.objects.create(
            code='stressfileprob',
            name='Stress File IO Problem',
            time_limit=1.0,
            memory_limit=65536,
            points=100,
            group=self.group,
            is_public=True,
        )
        file_prob.allowed_languages.add(self.lang_cpp)
        ProblemData.objects.create(
            problem=file_prob,
            grader='standard',
            grader_args=json.dumps({
                'io_method': 'file',
                'io_input_file': 'INPUT.TXT',
                'io_output_file': 'OUTPUT.TXT',
            }),
        )

        # Code mistakenly outputs to stdout instead of OUTPUT.TXT
        source = '#include <iostream>\nint main() { std::cout << "Wrong\\n"; return 0; }'
        res = execute_live_run(file_prob, 'CPP20', source, [
            {'id': 1, 'input': '123\n', 'expected': 'Expected'}
        ])
        self.assertEqual(res['status'], 'OK')
        # OUTPUT.TXT is empty, expected is 'Expected' -> WA
        self.assertEqual(res['results'][0]['status'], 'WA')
        self.assertEqual(res['results'][0]['output'].strip(), '')

    def test_rapid_concurrent_draft_updates(self):
        url = reverse('problem_draft', args=[self.problem.code])
        for i in range(10):
            payload = {
                'language': self.lang_cpp.id,
                'source': f'// Draft iteration {i}\nint main() {{ return {i}; }}',
                'testcases': [{'id': 1, 'input': f'{i}', 'expected': f'{i * 2}'}],
            }
            res = self.client.post(url, json.dumps(payload), content_type='application/json')
            self.assertEqual(res.status_code, 200)

        draft = ProblemDraft.objects.get(user=self.profile, problem=self.problem)
        self.assertIn('iteration 9', draft.source)
        self.assertEqual(draft.testcases[0]['input'], '9')

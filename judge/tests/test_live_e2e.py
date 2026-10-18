import json
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from judge.models import Language, Problem, ProblemDraft, ProblemGroup, Profile, Submission
from judge.utils.live_runner import execute_live_run


class LiveIdeEndToEndTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='e2euser', password='password123')
        self.profile = Profile.objects.create(user=self.user)
        self.group = ProblemGroup.objects.create(name='e2e-group', full_name='E2E Group')
        self.problem = Problem.objects.create(
            code='e2eprob',
            name='E2E Problem',
            time_limit=1.0,
            memory_limit=65536,
            points=100,
            group=self.group,
            is_public=True,
        )
        self.lang_cpp, _ = Language.objects.get_or_create(key='CPP20', defaults={'name': 'C++20'})
        self.lang_py, _ = Language.objects.get_or_create(key='PY3', defaults={'name': 'Python 3'})
        self.problem.allowed_languages.add(self.lang_cpp, self.lang_py)

    def test_e2e_page_render_and_draft_sync(self):
        self.client.login(username='e2euser', password='password123')
        page_url = reverse('problem_detail', args=[self.problem.code])
        res = self.client.get(page_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'live-ide-container')
        self.assertContains(res, 'live-testcase-drawer')

        # Save draft via draft endpoint
        draft_url = reverse('problem_draft', args=[self.problem.code])
        draft_data = {
            'language': self.lang_cpp.id,
            'source': '#include <iostream>\nint main() { return 0; }',
            'testcases': [{'id': 1, 'input': '5', 'expected': '5'}],
        }
        save_res = self.client.post(draft_url, json.dumps(draft_data), content_type='application/json')
        self.assertEqual(save_res.status_code, 200)

        # Verify draft saved in DB
        draft = ProblemDraft.objects.get(user=self.profile, problem=self.problem)
        self.assertEqual(draft.source, draft_data['source'])
        self.assertEqual(draft.language_id, self.lang_cpp.id)
        self.assertEqual(len(draft.testcases), 1)

    def test_e2e_live_run_verdicts(self):
        # 1. AC run
        ac_source = '#include <iostream>\nint main() { int x; if (std::cin >> x) std::cout << (x * 2) << std::endl; return 0; }'
        res_ac = execute_live_run(self.problem, 'CPP20', ac_source, [{'id': 1, 'input': '21\n', 'expected': '42'}])
        self.assertEqual(res_ac['status'], 'OK')
        self.assertEqual(res_ac['results'][0]['status'], 'AC')

        # 2. WA run
        res_wa = execute_live_run(self.problem, 'CPP20', ac_source, [{'id': 1, 'input': '21\n', 'expected': '99'}])
        self.assertEqual(res_wa['status'], 'OK')
        self.assertEqual(res_wa['results'][0]['status'], 'WA')

        # 3. CE run
        ce_source = 'int main() { syntax error }'
        res_ce = execute_live_run(self.problem, 'CPP20', ce_source, [{'id': 1, 'input': '', 'expected': ''}])
        self.assertEqual(res_ce['status'], 'CE')
        self.assertIsNotNone(res_ce['compile_error'])

        # 4. TLE run
        tle_source = '#include <iostream>\nint main() { while(true); return 0; }'
        res_tle = execute_live_run(self.problem, 'CPP20', tle_source, [{'id': 1, 'input': '', 'expected': ''}])
        self.assertEqual(res_tle['status'], 'OK')
        self.assertEqual(res_tle['results'][0]['status'], 'TLE')

    def test_e2e_live_run_file_io(self):
        from judge.models import ProblemData
        file_prob = Problem.objects.create(
            code='fileprob',
            name='File IO Problem',
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
                'io_input_file': 'TEST.INP',
                'io_output_file': 'TEST.OUT',
            }),
        )

        source = '''#include <fstream>
int main() {
    std::ifstream fin("TEST.INP");
    std::ofstream fout("TEST.OUT");
    int a, b;
    if (fin >> a >> b) fout << (a + b) << std::endl;
    return 0;
}'''
        res = execute_live_run(file_prob, 'CPP20', source, [{'id': 1, 'input': '12 13\n', 'expected': '25'}])
        self.assertEqual(res['status'], 'OK')
        self.assertEqual(res['results'][0]['status'], 'AC')
        self.assertEqual(res['results'][0]['output'].strip(), '25')

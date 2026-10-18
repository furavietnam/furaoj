import json
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from judge.models import Language, Problem, ProblemDraft, ProblemGroup, Profile, Submission, Solution
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
        self.assertContains(res, 'btn-tab-console')
        self.assertContains(res, 'console-panel')
        self.assertContains(res, 'console-log-content')

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

    def test_e2e_editorial_and_submission_links(self):
        self.client.login(username='e2euser', password='password123')
        page_url = reverse('problem_detail', args=[self.problem.code])

        # Without editorial: no editorial link
        res = self.client.get(page_url)
        self.assertNotContains(res, 'sidebar-editorial-link')
        self.assertContains(res, 'sidebar-menu-link')
        self.assertContains(res, 'target="_blank"')

        # With accessible editorial: editorial link and badge are rendered with target="_blank"
        Solution.objects.create(problem=self.problem, is_public=True, content='Editorial text')
        res_with_ed = self.client.get(page_url)
        self.assertContains(res_with_ed, 'sidebar-editorial-link')
        self.assertContains(res_with_ed, 'meta-badge-editorial')
        self.assertContains(res_with_ed, reverse('problem_editorial', args=[self.problem.code]))

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

    def test_live_sidebar_and_info_bar_rendering(self):
        self.client.login(username='e2euser', password='password123')
        page_url = reverse('problem_detail', args=[self.problem.code])
        res = self.client.get(page_url)
        self.assertEqual(res.status_code, 200)

        # 1. Info bar displays [CODE] Problem Name with status icon
        self.assertContains(res, 'problem-info-header')
        self.assertContains(res, '[e2eprob]')
        self.assertContains(res, 'E2E Problem')
        self.assertContains(res, 'unattempted-problem-color')

        # 2. Leftmost sidebar
        self.assertContains(res, 'live-sidebar-left')
        self.assertContains(res, 'btn-toggle-sidebar')
        self.assertContains(res, reverse('chronological_submissions', args=[self.problem.code]))
        self.assertContains(res, reverse('user_submissions', args=[self.problem.code, self.user.username]))
        self.assertContains(res, reverse('ranked_submissions', args=[self.problem.code]))

        # 3. Direct new-tab navigation
        self.assertContains(res, 'sidebar-menu-link')
        self.assertContains(res, 'target="_blank"')

        # 4. Donut chart canvas and context data
        self.assertContains(res, 'live-submission-donut')
        self.assertIn('submission_stats_json', res.context)
        self.assertIn('submission_colors_json', res.context)
        stats = json.loads(res.context['submission_stats_json'])
        self.assertIn('categories', stats)
        self.assertEqual(stats['total'], 0)

        # 5. Regular non-staff non-editor user does not see admin panel
        self.assertNotContains(res, 'id="live-sidebar-admin-section"')

    def test_admin_panel_visibility_for_editor_and_staff(self):
        from django.contrib.auth.models import Permission
        perm = Permission.objects.get(codename='edit_own_problem')
        self.user.user_permissions.add(perm)
        self.problem.authors.add(self.profile)

        self.client.login(username='e2euser', password='password123')
        page_url = reverse('problem_detail', args=[self.problem.code])
        res = self.client.get(page_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="live-sidebar-admin-section"')
        self.assertContains(res, reverse('problem_edit', args=[self.problem.code]))
        self.assertContains(res, reverse('problem_delete', args=[self.problem.code]))

        # Case B: Staff user
        self.problem.authors.remove(self.profile)
        self.user.user_permissions.remove(perm)
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        res_staff = self.client.get(page_url)
        self.assertEqual(res_staff.status_code, 200)
        self.assertContains(res_staff, 'id="live-sidebar-admin-section"')
        self.assertContains(res_staff, reverse('admin:judge_problem_change', args=[self.problem.id]))

    def test_problem_status_icon_solved_and_attempted(self):
        self.client.login(username='e2euser', password='password123')
        page_url = reverse('problem_detail', args=[self.problem.code])

        # Attempted (WA)
        sub_wa = Submission.objects.create(
            user=self.profile,
            problem=self.problem,
            language=self.lang_cpp,
            status='D',
            result='WA',
            points=0,
        )
        res_wa = self.client.get(page_url)
        self.assertContains(res_wa, 'attempted-problem-color')

        # Solved (AC)
        sub_ac = Submission.objects.create(
            user=self.profile,
            problem=self.problem,
            language=self.lang_cpp,
            status='D',
            result='AC',
            points=100,
        )
        from judge.caching import finished_submission
        finished_submission(sub_ac)
        res_ac = self.client.get(page_url)
        self.assertContains(res_ac, 'solved-problem-color')

        # Check that submission stats total is updated
        stats = json.loads(res_ac.context['submission_stats_json'])
        self.assertEqual(stats['total'], 2)

    def test_editorial_link_only_when_solution_exists(self):
        from judge.models import Solution
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(codename='edit_own_problem')
        self.user.user_permissions.add(perm)
        self.problem.authors.add(self.profile)
        self.client.login(username='e2euser', password='password123')

        page_url = reverse('problem_detail', args=[self.problem.code])
        editorial_url = reverse('problem_editorial', args=[self.problem.code])

        # 1. No solution exists -> link to editorial is not rendered (avoiding 404)
        res_no_sol = self.client.get(page_url)
        self.assertNotContains(res_no_sol, editorial_url)

        # 2. Solution exists -> editorial link is rendered
        Solution.objects.create(problem=self.problem, content='Official solution', is_public=True)
        res_with_sol = self.client.get(page_url)
        self.assertContains(res_with_sol, editorial_url)

    def test_workspace_panes_structure_and_status_link(self):
        self.client.login(username='e2euser', password='password123')
        page_url = reverse('problem_detail', args=[self.problem.code])
        res = self.client.get(page_url)

        # Panes wrapper ensures exact split calculations
        self.assertContains(res, 'id="live-workspace-panes"')
        self.assertContains(res, 'class="problem-status-link"')
        self.assertContains(res, 'target="_blank"')

        # Theme styles compiled into stylesheet
        import os
        from django.conf import settings
        scss_path = os.path.join(settings.BASE_DIR, 'resources', 'live-ide.scss')
        with open(scss_path, 'r', encoding='utf-8') as f:
            scss_content = f.read()
        self.assertIn('body[data-theme="dark"]', scss_content)
        self.assertIn('body[data-theme="auto"]', scss_content)
        self.assertIn('prefers-color-scheme: dark', scss_content)


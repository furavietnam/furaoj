import json
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from judge.models import Language, Problem, ProblemDraft, ProblemGroup, Profile


class LiveViewsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='viewuser', password='password123')
        self.profile = Profile.objects.create(user=self.user)
        self.group = ProblemGroup.objects.create(name='views-group', full_name='Views Group')
        self.problem = Problem.objects.create(
            code='viewprob',
            name='View Problem',
            time_limit=1.0,
            memory_limit=65536,
            points=100,
            group=self.group,
            is_public=True,
        )
        self.lang = Language.objects.create(key='CPP20', name='C++20')
        self.problem.allowed_languages.add(self.lang)

    def test_live_run_unauthenticated(self):
        url = reverse('problem_live_run', args=[self.problem.code])
        response = self.client.post(url, json.dumps({}), content_type='application/json')
        # Expect redirect to login (302) or 403
        self.assertIn(response.status_code, [302, 403])

    def test_live_run_oversized_payload(self):
        self.client.login(username='viewuser', password='password123')
        url = reverse('problem_live_run', args=[self.problem.code])
        payload = {
            'language': self.lang.id,
            'source': 'a' * (1024 * 1024 + 10),
            'testcases': [],
        }
        response = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_live_run_too_many_testcases(self):
        self.client.login(username='viewuser', password='password123')
        url = reverse('problem_live_run', args=[self.problem.code])
        payload = {
            'language': self.lang.id,
            'source': 'int main() {}',
            'testcases': [{'id': i, 'input': ''} for i in range(6)],
        }
        response = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_live_draft_save_and_load(self):
        self.client.login(username='viewuser', password='password123')
        url = reverse('problem_draft', args=[self.problem.code])

        # Initial load: empty
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'OK')
        self.assertEqual(data['source'], '')

        # Save draft
        save_payload = {
            'language': self.lang.id,
            'source': 'int main() { return 0; }',
            'testcases': [{'id': 1, 'input': '1 2', 'expected': '3'}],
        }
        post_res = self.client.post(url, json.dumps(save_payload), content_type='application/json')
        self.assertEqual(post_res.status_code, 200)

        # Load draft: should have saved data
        res2 = self.client.get(url)
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2['source'], 'int main() { return 0; }')
        self.assertEqual(data2['language'], self.lang.id)
        self.assertEqual(len(data2['testcases']), 1)

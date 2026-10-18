from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from judge.models import Problem, ProblemGroup, Profile, Language, ProblemDraft


class ProblemDraftTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='testuser_draft')
        self.profile = Profile.objects.create(user=self.user)
        self.group = ProblemGroup.objects.create(name='test-group', full_name='Test Group')
        self.problem = Problem.objects.create(
            code='testprob_draft',
            name='Test Problem Draft',
            time_limit=1.0,
            memory_limit=65536,
            points=100,
            group=self.group,
        )
        self.lang = Language.objects.create(key='CPP20', name='C++20')

    def test_create_and_update_draft(self):
        draft = ProblemDraft.objects.create(
            user=self.profile,
            problem=self.problem,
            language=self.lang,
            source='int main() {}',
            testcases=[{'input': '1 2', 'expected': '3'}]
        )
        self.assertEqual(draft.testcases[0]['input'], '1 2')
        self.assertEqual(ProblemDraft.objects.filter(user=self.profile, problem=self.problem).count(), 1)

    def test_draft_max_size_validation(self):
        huge_source = 'a' * (1024 * 1024 + 10)
        draft = ProblemDraft(
            user=self.profile,
            problem=self.problem,
            source=huge_source,
        )
        with self.assertRaises(ValidationError):
            draft.clean()

    def test_draft_max_testcases_validation(self):
        too_many_cases = [{'input': str(i), 'expected': ''} for i in range(6)]
        draft = ProblemDraft(
            user=self.profile,
            problem=self.problem,
            testcases=too_many_cases,
        )
        with self.assertRaises(ValidationError):
            draft.clean()

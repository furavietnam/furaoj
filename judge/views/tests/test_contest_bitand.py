from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models.tests.util import (
    create_contest,
    create_contest_problem,
    create_problem,
    create_user,
)


class ContestDetailBitandTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._now = timezone.now()
        cls.staff = create_user(username='staff_bitand', is_staff=True, is_superuser=True)

        cls.contest = create_contest(
            key='bitand_test',
            start_time=cls._now - timezone.timedelta(days=10),
            end_time=cls._now + timezone.timedelta(days=10),
            is_visible=True,
            authors=('staff_bitand',),
            run_pretests_only=False,
        )

        cls.problem1 = create_problem(code='bitand_prob1', authors=('staff_bitand',), partial=True)
        cls.problem2 = create_problem(code='bitand_prob2', authors=('staff_bitand',), partial=False)

        create_contest_problem(
            contest=cls.contest, problem=cls.problem1,
            order=1, points=100, partial=True, is_pretested=False,
        )
        create_contest_problem(
            contest=cls.contest, problem=cls.problem2,
            order=2, points=200, partial=False, is_pretested=True,
        )

    def test_contest_detail_loads_without_bitand_error(self):
        """ContestDetail metadata aggregation must not raise ProgrammingError on boolean & boolean."""
        self.client.force_login(self.staff.user)
        response = self.client.get(reverse('contest_view', args=(self.contest.key,)))
        self.assertEqual(response.status_code, 200)
        metadata = response.context['metadata']
        self.assertIn('has_partials', metadata)
        self.assertIn('has_pretests', metadata)

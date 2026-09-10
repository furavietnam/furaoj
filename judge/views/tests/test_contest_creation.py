from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models.tests.util import create_user


class CreateContestFormUserTestCase(TestCase):
    """Test that CreateContest.post() passes user to ContestForm."""

    @classmethod
    def setUpTestData(cls):
        cls._now = timezone.now()
        cls.staff = create_user(
            username='contest_creator',
            is_staff=True,
            is_superuser=True,
        )

    def test_post_passes_user_to_form(self):
        """ContestForm must receive user kwarg during POST for clean() to work."""
        self.client.force_login(self.staff.user)

        # GET to get CSRF token
        response = self.client.get(reverse('contest_new'))
        self.assertEqual(response.status_code, 200)

        post_data = {
            'key': 'test_user_pass',
            'name': 'Test User Pass',
            'start_time': self._now.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': (self._now + timezone.timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'scoreboard_visibility': 'V',
            'format_name': 'default',
            'description': '',
            'access_code': '',
            'private_contestants': [],
            'contest_problems-TOTAL_FORMS': '0',
            'contest_problems-INITIAL_FORMS': '0',
            'contest_problems-MIN_NUM_FORMS': '0',
            'contest_problems-MAX_NUM_FORMS': '26',
        }

        with patch('judge.views.contests.ContestForm') as MockForm:
            mock_form_instance = MagicMock()
            mock_form_instance.is_valid.return_value = False
            MockForm.return_value = mock_form_instance

            response = self.client.post(reverse('contest_new'), post_data)

            # The form MUST be called with user=request.user
            _, kwargs = MockForm.call_args
            self.assertIn('user', kwargs, 'ContestForm must receive user kwarg during POST')
            self.assertEqual(kwargs['user'], self.staff.user)

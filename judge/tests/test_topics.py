from django.test import TestCase
from django.contrib.auth.models import User
from judge.models import Problem, ProblemGroup, Profile, Topic, TopicProblem


class TopicModelTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='regular_user', password='password')
        self.profile = Profile.objects.create(user=self.user)
        self.staff_user = User.objects.create_user(username='staff_user', password='password', is_staff=True)
        self.group = ProblemGroup.objects.create(name='test-group', full_name='Test Group')

        self.problem1 = Problem.objects.create(
            code='prob1',
            name='Problem One',
            time_limit=1.0,
            memory_limit=65536,
            points=10,
            group=self.group,
        )
        self.problem2 = Problem.objects.create(
            code='prob2',
            name='Problem Two',
            time_limit=1.0,
            memory_limit=65536,
            points=25,
            group=self.group,
        )

    def test_topic_creation_and_stats(self):
        topic = Topic.objects.create(
            name='Dynamic Programming Basics',
            slug='dp-basics',
            description='Learn DP from scratch',
            is_public=True,
        )
        self.assertEqual(topic.problem_count, 0)
        self.assertEqual(topic.total_points, 0)

        TopicProblem.objects.create(topic=topic, problem=self.problem1, order=1)
        TopicProblem.objects.create(topic=topic, problem=self.problem2, order=2)

        topic.update_stats()
        topic.refresh_from_db()
        self.assertEqual(topic.problem_count, 2)
        self.assertEqual(topic.total_points, 35)

    def test_topic_permissions(self):
        topic = Topic.objects.create(name='Math', slug='math')
        self.assertFalse(topic.is_editable_by(self.user))
        self.assertTrue(topic.is_editable_by(self.staff_user))

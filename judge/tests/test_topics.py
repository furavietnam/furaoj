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


class TopicViewsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='normal_user', password='password123')
        self.profile = Profile.objects.create(user=self.user)
        self.staff_user = User.objects.create_user(username='admin_user', password='password123', is_staff=True)
        self.staff_profile = Profile.objects.create(user=self.staff_user)

        self.group = ProblemGroup.objects.create(name='default-group', full_name='Default Group')
        self.p1 = Problem.objects.create(code='p1', name='Problem 1', points=10, time_limit=1.0, memory_limit=65536, group=self.group)
        self.p2 = Problem.objects.create(code='p2', name='Problem 2', points=20, time_limit=1.0, memory_limit=65536, group=self.group)
        self.p3 = Problem.objects.create(code='p3', name='Problem 3', points=30, time_limit=1.0, memory_limit=65536, group=self.group)

        self.topic = Topic.objects.create(
            name='Graph Algorithms',
            slug='graph-algorithms',
            description='Shortest paths and MST',
            is_public=True,
        )
        TopicProblem.objects.create(topic=self.topic, problem=self.p1, order=1)
        TopicProblem.objects.create(topic=self.topic, problem=self.p2, order=2)
        self.topic.update_stats()

    def test_topic_list_view(self):
        response = self.client.get('/topics/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Graph Algorithms')
        self.assertContains(response, 'topic-grid')
        self.assertContains(response, 'topic-card')

    def test_topic_detail_view_by_slug_and_reject_numeric_id(self):
        # Access by numeric ID should return 404 (only slug is allowed)
        res_id = self.client.get(f'/topic/{self.topic.id}/')
        self.assertEqual(res_id.status_code, 404)

        # Access by Slug should return 200
        res_slug = self.client.get(f'/topic/{self.topic.slug}/')
        self.assertEqual(res_slug.status_code, 200)
        self.assertContains(res_slug, 'Graph Algorithms')
        self.assertContains(res_slug, 'p1')
        self.assertContains(res_slug, 'p2')
        self.assertNotContains(res_slug, 'topic-quick-add-form')

        # Staff user should see the add form
        self.client.login(username='admin_user', password='password123')
        res_staff = self.client.get(f'/topic/{self.topic.slug}/')
        self.assertEqual(res_staff.status_code, 200)
        self.assertContains(res_staff, 'topic-quick-add-form')

    def test_topic_add_problem_permission(self):
        # Anonymous
        res_anon = self.client.post(f'/topic/{self.topic.slug}/add-problem/', {'problem_code': 'p3'})
        self.assertIn(res_anon.status_code, [302, 403])

        # Regular user
        self.client.login(username='normal_user', password='password123')
        res_reg = self.client.post(f'/topic/{self.topic.slug}/add-problem/', {'problem_code': 'p3'})
        self.assertEqual(res_reg.status_code, 403)

        # Staff user
        self.client.login(username='admin_user', password='password123')
        res_staff = self.client.post(f'/topic/{self.topic.slug}/add-problem/', {'problem_code': 'p3'})
        self.assertIn(res_staff.status_code, [200, 302])
        self.assertEqual(self.topic.topic_problems.filter(problem=self.p3).count(), 1)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.problem_count, 3)
        self.assertEqual(self.topic.total_points, 60)

    def test_topic_remove_problem(self):
        self.client.login(username='admin_user', password='password123')
        res = self.client.post(f'/topic/{self.topic.slug}/remove-problem/{self.p1.id}/')
        self.assertIn(res.status_code, [200, 302])
        self.assertEqual(self.topic.topic_problems.filter(problem=self.p1).count(), 0)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.problem_count, 1)
        self.assertEqual(self.topic.total_points, 20)

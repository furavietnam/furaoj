from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from judge.views.ranked_submission import RankedSubmissions
from judge.views.submission import ProblemSubmissions


class RankedSubmissionsQueryTestCase(SimpleTestCase):
    def _build_query(self, vendor):
        view = RankedSubmissions()
        view.problem = Mock(id=123)
        view.selected_languages = set()
        view.request = Mock()
        view.__dict__['is_contest_scoped'] = False

        queryset = Mock()
        queryset.filter.return_value = queryset
        queryset.order_by.return_value = queryset

        with patch.object(ProblemSubmissions, 'get_queryset', return_value=queryset), \
                patch('django.db.connection.vendor', vendor), \
                patch('judge.views.ranked_submission.join_sql_subquery') as join_sql:
            view.get_queryset()

        return join_sql.call_args.kwargs['subquery']

    def test_rank_query_uses_standard_inner_joins(self):
        query = self._build_query('postgresql')

        self.assertNotIn('STRAIGHT_JOIN', query)
        self.assertEqual(query.count('INNER JOIN'), 2)
        self.assertIn('SELECT MIN(sub.id) AS id', query)

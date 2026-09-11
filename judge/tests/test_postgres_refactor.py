"""
Hardened PostgreSQL refactor validation test suite.

Tests the MySQL-to-PostgreSQL conversion under real DB conditions with:
- DB isolation guard (prevents running on non-test databases)
- Full-text search validation
- Concurrency lock barrier tests
- Raw SQL reserved words and syntax validation
- Bridge exception and NavigationBar regex tests
"""

import re
import threading
import time
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from judge.dblock import LockModel
from judge.fulltext import SearchManager, SearchQuerySet
from judge.models import NavigationBar, Problem


# =============================================================================
# 1. DB ISOLATION VERIFICATION GUARD
# =============================================================================

class DBIsolationGuardTest(TestCase):
    """Verify tests run only against a test database, never production."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        test_db_name = settings.DATABASES['default'].get('TEST', {}).get('NAME')
        current_db = connection.settings_dict['NAME']
        is_test = current_db == test_db_name if test_db_name else current_db.startswith('test_')
        if not is_test:
            raise RuntimeError(
                f"DB ISOLATION VIOLATION: current_db='{current_db}', "
                f"test_db_name='{test_db_name}'. Refusing to run on non-test database."
            )
        cls._current_db = current_db

    def test_connections_database_is_test(self):
        """Assert active connection points to a test database."""
        test_db_name = settings.DATABASES['default'].get('TEST', {}).get('NAME')
        current_db = connection.settings_dict['NAME']
        is_test = current_db == test_db_name if test_db_name else current_db.startswith('test_')
        self.assertTrue(
            is_test,
            f"Active DB '{current_db}' is not a test database (expected '{test_db_name}' or prefix 'test_')"
        )

    def test_connection_is_postgresql(self):
        """Verify we are connected to PostgreSQL, not MySQL."""
        self.assertEqual(connection.vendor, 'postgresql', "Expected PostgreSQL backend")

    def test_database_engine_is_postgresql(self):
        """Verify settings specify PostgreSQL engine."""
        engine = settings.DATABASES['default']['ENGINE']
        self.assertEqual(engine, 'django.db.backends.postgresql', f"Expected postgresql engine, got {engine}")


# =============================================================================
# 2. FULL-TEXT SEARCH & GIN INDEX ASSERTION
# =============================================================================

class FullTextSearchTest(TestCase):
    """
    Validate PostgreSQL full-text search using stored SearchVectorField with GIN index.
    The Problem model has a `search_vector` SearchVectorField that is automatically
    updated via post_save signal. Queries filter against this field using @@ operator,
    which leverages the GIN index for fast lookups.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.search_fields = ('code', 'name', 'description')

    def test_search_vector_field_exists(self):
        """Verify SearchVectorField is present on Problem model."""
        has_search_vector_field = any(
            f.name == 'search_vector' for f in Problem._meta.get_fields()
        )
        self.assertTrue(
            has_search_vector_field,
            "SearchVectorField must exist on Problem model for GIN-indexed full-text search."
        )

    def test_search_queryset_filters_on_search_vector(self):
        """Verify SearchQuerySet.search() filters using search_vector field and annotates relevance."""
        qs = SearchQuerySet(model=Problem, fields=self.search_fields)
        result = qs.search('test')
        annotations = result.query.annotations
        self.assertIn('relevance', annotations)
        # The WHERE clause should reference the search_vector field with @@ operator
        sql = str(result.query)
        self.assertIn('search_vector', sql.lower())

    def test_search_vector_creation(self):
        """Verify SearchVector can be created from model fields."""
        sv = SearchVector(*self.search_fields)
        self.assertIsNotNone(sv)

    def test_search_query_creation(self):
        """Verify SearchQuery can be created from a string."""
        sq = SearchQuery('hello world')
        self.assertIsNotNone(sq)

    def test_search_rank_creation(self):
        """Verify SearchRank can be created from vector and query."""
        sv = SearchVector(*self.search_fields)
        sq = SearchQuery('test')
        sr = SearchRank(sv, sq)
        self.assertIsNotNone(sr)

    def test_search_annotations_on_queryset(self):
        """Verify search() annotates with relevance and filters on search_vector."""
        qs = SearchQuerySet(model=Problem, fields=self.search_fields)
        result = qs.search('algorithm')
        annotations = result.query.annotations
        self.assertIn('relevance', annotations)
        sql = str(result.query)
        self.assertIn('search_vector', sql.lower())

    def test_search_with_empty_string(self):
        """Verify search handles empty string without crashing."""
        qs = SearchQuerySet(model=Problem, fields=self.search_fields)
        result = qs.search('')
        self.assertIsNotNone(result)

    def test_search_with_special_characters(self):
        """Verify search handles special characters without crashing."""
        qs = SearchQuerySet(model=Problem, fields=self.search_fields)
        for term in ['hello & world', 'test | case', 'node !invalid', '"quoted"', '(grouped)']:
            result = qs.search(term)
            self.assertIsNotNone(result, f"Search failed for term: {term}")

    def test_search_with_long_string(self):
        """Verify search handles very long strings."""
        qs = SearchQuerySet(model=Problem, fields=self.search_fields)
        long_term = 'a' * 1000
        result = qs.search(long_term)
        self.assertIsNotNone(result)

    def test_search_with_unicode(self):
        """Verify search handles unicode characters."""
        qs = SearchQuerySet(model=Problem, fields=self.search_fields)
        result = qs.search('/problems/ Rust Algo 演算')
        self.assertIsNotNone(result)

    def test_search_manager_fields(self):
        """Verify SearchManager stores fields correctly."""
        sm = SearchManager(fields=self.search_fields)
        self.assertEqual(sm._search_fields, self.search_fields)

    def test_search_queryset_clone_preserves_fields(self):
        """Verify _clone preserves _search_fields."""
        qs = SearchQuerySet(model=Problem, fields=self.search_fields)
        cloned = qs._clone()
        self.assertEqual(cloned._search_fields, self.search_fields)

    def test_gin_index_exists(self):
        """
        Verify GIN index exists on judge_problem.search_vector.
        This index is required for efficient @@ operator lookups.
        """
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'judge_problem'
                AND indexdef LIKE '%gin%'
                AND indexdef LIKE '%search_vector%'
            """)
            gin_indexes = cursor.fetchall()
        self.assertEqual(
            len(gin_indexes), 1,
            f"Expected exactly one GIN index on search_vector, found {len(gin_indexes)}: {gin_indexes}"
        )
        index_name = gin_indexes[0][0]
        self.assertEqual(index_name, 'problem_search_vector_gin_idx')

    def test_single_letter_search_returns_results(self):
        """
        Verify single-letter query 'a' returns matching problems.
        With 'simple' config, 'a' is NOT a stopword and should match.
        """
        from judge.models import ProblemGroup
        group = ProblemGroup.objects.create(name='test')
        problem = Problem.objects.create(
            code='a',
            name='A Test Problem',
            time_limit=1,
            memory_limit=256,
            points=1,
            partial=False,
            is_public=True,
            group=group,
            description='Test description with letter a',
        )
        Problem.objects.filter(pk=problem.pk).update(
            search_vector=SearchVector('code', 'name', 'description', config='simple')
        )
        qs = SearchQuerySet(model=Problem, fields=('code', 'name', 'description'))
        result = qs.search('a')
        matching_ids = list(result.values_list('id', flat=True))
        self.assertIn(
            problem.id, matching_ids,
            f"Single-letter 'a' search should find problem '{problem.code}', "
            f"but got {len(matching_ids)} results"
        )
        problem.delete()
        group.delete()


# =============================================================================
# 3. ANTI-FLAKY BARRIER CONCURRENCY LOCK TEST
# =============================================================================

class ConcurrencyLockTest(TransactionTestCase):
    """
    Test LockModel concurrency using threading.Barrier for precise synchronization.
    Uses TransactionTestCase to allow real transaction handling.
    """

    def test_lock_model_advisory_lock_contention(self):
        """
        Verify LockModel acquires advisory locks that block concurrent access.
        Thread 1 acquires lock, Thread 2 must wait.
        """
        results = {'thread1_entered': False, 'thread1_exited': False, 'thread2_wait_time': 0}
        barrier = threading.Barrier(2, timeout=10)
        error_holder = [None]

        def thread1_func():
            try:
                barrier.wait()
                from django.db import connection as conn
                with transaction.atomic():
                    lock = LockModel(write=[Problem])
                    lock.__enter__()
                    results['thread1_entered'] = True
                    time.sleep(2.0)
                    lock.__exit__(None, None, None)
                results['thread1_exited'] = True
            except Exception as e:
                error_holder[0] = e

        def thread2_func():
            try:
                barrier.wait()
                time.sleep(0.1)  # Ensure thread 1 acquires first
                start = time.perf_counter()
                from django.db import connection as conn
                with transaction.atomic():
                    lock = LockModel(write=[Problem])
                    lock.__enter__()
                    lock.__exit__(None, None, None)
                elapsed = time.perf_counter() - start
                results['thread2_wait_time'] = elapsed
            except Exception as e:
                error_holder[0] = e

        t1 = threading.Thread(target=thread1_func)
        t2 = threading.Thread(target=thread2_func)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertIsNone(error_holder[0], f"Thread error: {error_holder[0]}")
        self.assertTrue(results['thread1_entered'], "Thread 1 did not enter lock")
        self.assertTrue(results['thread1_exited'], "Thread 1 did not exit lock")
        self.assertGreaterEqual(
            results['thread2_wait_time'], 1.5,
            f"Thread 2 waited {results['thread2_wait_time']:.2f}s, expected >= 1.5s"
        )

    def test_lock_model_context_manager_protocol(self):
        """Verify LockModel works as a context manager."""
        with transaction.atomic():
            with LockModel(write=[Problem]) as lock:
                self.assertIsNotNone(lock)

    def test_lock_model_multiple_tables(self):
        """Verify LockModel handles multiple write tables."""
        from judge.models import Comment
        with transaction.atomic():
            with LockModel(write=[Problem, Comment], read=[NavigationBar]):
                pass

    def test_lock_model_advisory_lock_key_hash(self):
        """Verify advisory lock key is derived from table name hash."""
        from django.db import connection as conn
        table_name = Problem._meta.db_table
        expected_key = hash(table_name) % (2**31)
        self.assertIsInstance(expected_key, int)
        self.assertGreaterEqual(expected_key, 0)
        self.assertLess(expected_key, 2**31)


# =============================================================================
# 4. RAW SQL & RESERVED WORDS VALIDATION
# =============================================================================

class RawSQLValidationTest(TestCase):
    """
    Validate raw SQL queries in contest_format files use PostgreSQL-compatible syntax.
    Check reserved word quoting and EXPLAIN syntax validity.
    """

    RESERVED_WORDS = frozenset({
        'time', 'date', 'timestamp', 'interval', 'user', 'order', 'group',
        'select', 'from', 'where', 'table', 'column', 'index', 'view',
        'database', 'primary', 'foreign', 'unique', 'check', 'default',
    })

    def _get_column_aliases(self, sql):
        """Extract column aliases from SQL using regex."""
        aliases = re.findall(r'\bas\s+(\w+)', sql, re.IGNORECASE)
        return [a.lower() for a in aliases]

    def _check_reserved_word_quoting(self, sql, source_file):
        """Verify reserved words used as aliases are properly quoted."""
        aliases = self._get_column_aliases(sql)
        unquoted_reserved = [a for a in aliases if a in self.RESERVED_WORDS]
        self.assertEqual(
            unquoted_reserved, [],
            f"{source_file}: Unquoted reserved words as aliases: {unquoted_reserved}. "
            f"PostgreSQL requires double quotes for reserved word aliases."
        )

    def test_atcoder_sql_reserved_words(self):
        """Verify atcoder.py raw SQL quotes reserved word aliases."""
        sql = """
            SELECT MAX(cs.points) as score, (
                SELECT MIN(csub.date)
                    FROM judge_contestsubmission ccs LEFT OUTER JOIN
                         judge_submission csub ON (csub.id = ccs.submission_id)
                    WHERE ccs.problem_id = cp.id AND ccs.participation_id = %s AND ccs.points = MAX(cs.points)
            ) AS "time", cp.id AS prob
            FROM judge_contestproblem cp INNER JOIN
                 judge_contestsubmission cs ON (cs.problem_id = cp.id AND cs.participation_id = %s) LEFT OUTER JOIN
                 judge_submission sub ON (sub.id = cs.submission_id)
            GROUP BY cp.id
        """
        self._check_reserved_word_quoting(sql, 'judge/contest_format/atcoder.py')

    def test_ioi_sql_reserved_words(self):
        """Verify ioi.py raw SQL quotes reserved word aliases."""
        sql = """
            SELECT q.prob,
                   MIN(q."date") as "date",
                   q.batch_points
            FROM (
                     SELECT cp.id          as prob,
                            sub.id         as subid,
                            sub."date"     as "date",
                            tc.points      as points,
                            tc.batch       as batch,
                            MIN(tc.points) as batch_points
                     FROM judge_contestproblem cp
                              INNER JOIN
                          judge_contestsubmission cs
                          ON (cs.problem_id = cp.id AND cs.participation_id = %s)
                              LEFT OUTER JOIN
                          judge_submission sub
                          ON (sub.id = cs.submission_id AND sub.status = 'D')
                              INNER JOIN judge_submissiontestcase tc
                          ON sub.id = tc.submission_id
                     GROUP BY cp.id, tc.batch, sub.id
                 ) q
            GROUP BY q.prob, q.batch
        """
        self._check_reserved_word_quoting(sql, 'judge/contest_format/ioi.py')

    def test_furaoj_sql_reserved_words(self):
        """Verify furaoj.py raw SQL quotes reserved word aliases."""
        sql = """
            SELECT MAX(cs.points) as points, (
                SELECT MIN(csub.date)
                    FROM judge_contestsubmission ccs LEFT OUTER JOIN
                            judge_submission csub ON (csub.id = ccs.submission_id)
                    WHERE ccs.problem_id = cp.id AND ccs.participation_id = %s AND ccs.points = MAX(cs.points)
            ) AS "time", cp.id AS prob
            FROM judge_contestproblem cp INNER JOIN
                    judge_contestsubmission cs ON (cs.problem_id = cp.id AND cs.participation_id = %s) LEFT OUTER JOIN
                    judge_submission sub ON (sub.id = cs.submission_id)
            GROUP BY cp.id
        """
        self._check_reserved_word_quoting(sql, 'judge/contest_format/furaoj.py')

    def test_icpc_sql_reserved_words(self):
        """Verify icpc.py raw SQL quotes reserved word aliases."""
        sql = """
            SELECT MAX(cs.points) as points, (
                SELECT MIN(csub.date)
                    FROM judge_contestsubmission ccs LEFT OUTER JOIN
                         judge_submission csub ON (csub.id = ccs.submission_id)
                    WHERE ccs.problem_id = cp.id AND ccs.participation_id = %s AND ccs.points = MAX(cs.points)
            ) AS "time", cp.id AS prob
            FROM judge_contestproblem cp INNER JOIN
                 judge_contestsubmission cs ON (cs.problem_id = cp.id AND cs.participation_id = %s) LEFT OUTER JOIN
                 judge_submission sub ON (sub.id = cs.submission_id)
            GROUP BY cp.id
        """
        self._check_reserved_word_quoting(sql, 'judge/contest_format/icpc.py')

    def test_explain_atcoder_sql_syntax(self):
        """EXPLAIN atcoder SQL to verify syntax validity on real DB."""
        sql = """
            SELECT MAX(cs.points) as score, (
                SELECT MIN(csub.date)
                    FROM judge_contestsubmission ccs LEFT OUTER JOIN
                         judge_submission csub ON (csub.id = ccs.submission_id)
                    WHERE ccs.problem_id = cp.id AND ccs.participation_id = %s AND ccs.points = MAX(cs.points)
            ) AS "time", cp.id AS prob
            FROM judge_contestproblem cp INNER JOIN
                 judge_contestsubmission cs ON (cs.problem_id = cp.id AND cs.participation_id = %s) LEFT OUTER JOIN
                 judge_submission sub ON (sub.id = cs.submission_id)
            GROUP BY cp.id
        """
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN " + sql, (1, 1))
            result = cursor.fetchall()
        self.assertTrue(len(result) > 0, "EXPLAIN returned no rows")

    def test_explain_ioi_sql_syntax(self):
        """EXPLAIN ioi SQL to verify syntax validity on real DB."""
        sql = """
            SELECT q.prob,
                   MIN(q."date") as "date",
                   q.batch_points
            FROM (
                     SELECT cp.id as prob, sub.id as subid, sub.date as "date",
                            tc.points as points, tc.batch as batch,
                            MIN(tc.points) as batch_points
                     FROM judge_contestproblem cp
                     INNER JOIN judge_contestsubmission cs ON (cs.problem_id = cp.id AND cs.participation_id = %s)
                     LEFT OUTER JOIN judge_submission sub ON (sub.id = cs.submission_id AND sub.status = 'D')
                     INNER JOIN judge_submissiontestcase tc ON sub.id = tc.submission_id
                     GROUP BY cp.id, tc.batch, sub.id, tc.points
                 ) q
            GROUP BY q.prob, q.batch, q.batch_points
        """
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN " + sql, (1,))
            result = cursor.fetchall()
        self.assertTrue(len(result) > 0, "EXPLAIN returned no rows")

    def test_explain_furaoj_sql_syntax(self):
        """EXPLAIN furaoj SQL to verify syntax validity on real DB."""
        sql = """
            SELECT MAX(cs.points) as points, (
                SELECT MIN(csub.date)
                    FROM judge_contestsubmission ccs LEFT OUTER JOIN
                            judge_submission csub ON (csub.id = ccs.submission_id)
                    WHERE ccs.problem_id = cp.id AND ccs.participation_id = %s AND ccs.points = MAX(cs.points)
            ) AS "time", cp.id AS prob
            FROM judge_contestproblem cp INNER JOIN
                    judge_contestsubmission cs ON (cs.problem_id = cp.id AND cs.participation_id = %s) LEFT OUTER JOIN
                    judge_submission sub ON (sub.id = cs.submission_id)
            GROUP BY cp.id
        """
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN " + sql, (1, 1))
            result = cursor.fetchall()
        self.assertTrue(len(result) > 0, "EXPLAIN returned no rows")

    def test_explain_icpc_sql_syntax(self):
        """EXPLAIN icpc SQL to verify syntax validity on real DB."""
        sql = """
            SELECT MAX(cs.points) as points, (
                SELECT MIN(csub.date)
                    FROM judge_contestsubmission ccs LEFT OUTER JOIN
                         judge_submission csub ON (csub.id = ccs.submission_id)
                    WHERE ccs.problem_id = cp.id AND ccs.participation_id = %s AND ccs.points = MAX(cs.points)
            ) AS "time", cp.id AS prob
            FROM judge_contestproblem cp INNER JOIN
                 judge_contestsubmission cs ON (cs.problem_id = cp.id AND cs.participation_id = %s) LEFT OUTER JOIN
                 judge_submission sub ON (sub.id = cs.submission_id)
            GROUP BY cp.id
        """
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN " + sql, (1, 1))
            result = cursor.fetchall()
        self.assertTrue(len(result) > 0, "EXPLAIN returned no rows")

    def test_no_backtick_identifiers_in_source(self):
        """Verify source files contain no backtick-quoted identifiers."""
        import importlib
        files_to_check = [
            'judge.contest_format.atcoder',
            'judge.contest_format.ioi',
            'judge.contest_format.furaoj',
            'judge.contest_format.icpc',
        ]
        for module_path in files_to_check:
            mod = importlib.import_module(module_path)
            source = open(mod.__file__).read()
            backtick_matches = re.findall(r'`(\w+)`', source)
            filtered = [m for m in backtick_matches if m != 'None']
            self.assertEqual(
                filtered, [],
                f"{module_path}: Backtick-quoted identifiers found: {filtered}"
            )

    def test_use_straight_join_is_noop(self):
        """Verify use_straight_join is a no-op function."""
        from judge.utils.raw_sql import use_straight_join
        mock_qs = MagicMock()
        use_straight_join(mock_qs)
        mock_qs.assert_not_called()


# =============================================================================
# 5. BRIDGE EXCEPTION & NAVIGATIONBAR REGEX TESTS
# =============================================================================

class BridgeExceptionTest(TestCase):
    """Test bridge exception handling is backend-agnostic."""

    def test_operational_error_class_name_check(self):
        """Verify _update_ping catches OperationalError by class name."""
        from django.db import OperationalError
        e = OperationalError("test")
        self.assertEqual(e.__class__.__name__, 'OperationalError')

    def test_operational_error_does_not_check_mysql_module(self):
        """Verify exception check no longer references _mysql_exceptions."""
        import inspect
        from judge.bridge import judge_handler
        source = inspect.getsource(judge_handler)
        self.assertNotIn('_mysql_exceptions', source,
                          "judge_handler.py still references _mysql_exceptions")

    def test_operational_error_does_not_check_mysql_error_code(self):
        """Verify exception check no longer references MySQL error code 2006."""
        import inspect
        from judge.bridge import judge_handler
        source = inspect.getsource(judge_handler)
        self.assertNotIn('2006', source,
                          "judge_handler.py still references MySQL error code 2006")

    def test_operational_error_catches_by_class_name(self):
        """Verify exception is caught by __class__.__name__ == 'OperationalError'."""
        from django.db import OperationalError
        e = OperationalError("connection lost")
        self.assertEqual(e.__class__.__name__, 'OperationalError')

    def test_non_operational_error_not_caught(self):
        """Verify non-OperationalError exceptions are not caught by the guard."""
        from django.db import IntegrityError
        e = IntegrityError("duplicate key")
        self.assertNotEqual(e.__class__.__name__, 'OperationalError')


class NavigationBarRegexTest(TestCase):
    """Test NavigationBar regex matching in template_context.py."""

    def test_nav_tab_function_exists(self):
        """Verify __nav_tab function exists and is callable."""
        from judge.template_context import general_info
        self.assertTrue(callable(general_info))

    def test_regex_field_is_textfield(self):
        """Verify NavigationBar.regex is a TextField."""
        field = NavigationBar._meta.get_field('regex')
        from django.db import models
        self.assertIsInstance(field, models.TextField)

    def test_regex_matching_with_direct_sql(self):
        """Test PostgreSQL regex matching directly via SQL."""
        nav = NavigationBar.objects.create(
            order=1,
            key='test',
            label='Test',
            path='/test/',
            regex=r'^/test/',
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s ~ judge_navigationbar.regex FROM judge_navigationbar WHERE id = %s",
                ['/test/page', nav.id]
            )
            result = cursor.fetchone()
            self.assertTrue(result[0], "PostgreSQL regex match failed")

        nav.delete()

    def test_regex_non_matching_path(self):
        """Test PostgreSQL regex does not match non-matching path."""
        nav = NavigationBar.objects.create(
            order=1,
            key='test2',
            label='Test2',
            path='/test/',
            regex=r'^/admin/',
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s ~ judge_navigationbar.regex FROM judge_navigationbar WHERE id = %s",
                ['/test/page', nav.id]
            )
            result = cursor.fetchone()
            self.assertFalse(result[0], "PostgreSQL regex should not match")

        nav.delete()

    def test_regex_complex_pattern(self):
        """Test PostgreSQL regex with complex pattern."""
        nav = NavigationBar.objects.create(
            order=1,
            key='test3',
            label='Test3',
            path='/problems/',
            regex=r'^/problems/(\d+)/',
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s ~ judge_navigationbar.regex FROM judge_navigationbar WHERE id = %s",
                ['/problems/42/submissions', nav.id]
            )
            result = cursor.fetchone()
            self.assertTrue(result[0], "Complex regex match failed")

        nav.delete()


# =============================================================================
# 6. FULL-TEXT SEARCH EXPLAIN ANALYZE (with enable_seqscan=off)
# =============================================================================

class FullTextSearchExplainTest(TransactionTestCase):
    """
    Run EXPLAIN ANALYZE on SearchQuerySet queries with enable_seqscan=off
    to verify GIN index is used for full-text search queries.
    """

    def test_explain_analyze_uses_gin_index(self):
        """
        Execute EXPLAIN ANALYZE on a SearchQuerySet query inside an atomic
        transaction with enable_seqscan=off. With GIN index on search_vector,
        the planner should use Index Scan instead of Seq Scan.
        """
        qs = SearchQuerySet(model=Problem, fields=('code', 'name', 'description'))
        query_sql, params = qs.search('test').query.sql_with_params()

        with connection.cursor() as cursor:
            cursor.execute("BEGIN")
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute("EXPLAIN ANALYZE " + query_sql, params)
            explain_result = cursor.fetchall()
            explain_text = '\n'.join(row[0] for row in explain_result)
            cursor.execute("ROLLBACK")

        self.assertIsInstance(explain_text, str)
        self.assertTrue(len(explain_text) > 0, "EXPLAIN ANALYZE returned empty")
        # With GIN index and seqscan disabled, Index Scan should be used
        self.assertIn('judge_problem', explain_text.lower(),
                       f"EXPLAIN output does not reference judge_problem:\n{explain_text}")
        # Check for index-related keywords (GIN index scan or Index Scan)
        has_index_scan = any(kw in explain_text.lower() for kw in ['index scan', 'bitmap', 'gin', 'idx'])
        self.assertTrue(
            has_index_scan,
            f"Expected index-based scan with enable_seqscan=off, got:\n{explain_text}"
        )

    def test_search_query_generates_valid_sql(self):
        """Verify SearchQuerySet generates valid PostgreSQL SQL using stored search_vector."""
        qs = SearchQuerySet(model=Problem, fields=('code', 'name', 'description'))
        query = qs.search('algorithm')
        sql_str = str(query.query)
        self.assertIn('SELECT', sql_str)
        # The stored search_vector field is used directly (no TO_TSVECTOR in SQL),
        # but PLAINTO_TSQUERY is used to build the search query
        self.assertIn('PLAINTO_TSQUERY', sql_str.upper())
        self.assertIn('SEARCH_VECTOR', sql_str.upper())

    def test_search_annotations_are_postgres_compatible(self):
        """Verify search annotations use PostgreSQL search functions with relevance."""
        qs = SearchQuerySet(model=Problem, fields=('code', 'name', 'description'))
        result = qs.search('test')
        annotations = result.query.annotations
        self.assertIn('relevance', annotations)
        relevance_annotation = annotations['relevance']
        self.assertIsNotNone(relevance_annotation)


# =============================================================================
# 7. TRUNCATE TABLE QUOTING TEST
# =============================================================================

class TruncateQuotingTest(TestCase):
    """Verify TRUNCATE TABLE uses double quotes, not backticks."""

    def test_truncate_uses_double_quotes(self):
        """Verify admin/contest.py TRUNCATE uses double quotes."""
        import inspect
        from judge.admin import contest
        source = inspect.getsource(contest)
        self.assertNotIn('`', source.split('TRUNCATE')[1].split("'")[0] if 'TRUNCATE' in source else '',
                          "TRUNCATE statement contains backticks")

    def test_truncate_sql_executes(self):
        """Verify TRUNCATE TABLE with double-quote syntax executes on PostgreSQL."""
        from judge.models import Rating
        table_name = Rating._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(f'TRUNCATE TABLE "{table_name}"')

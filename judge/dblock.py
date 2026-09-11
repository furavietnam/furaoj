from django.db import connection, transaction


class LockModel(object):
    def __init__(self, write, read=()):
        self.write_tables = [model._meta.db_table for model in write]
        self.read_tables = [model._meta.db_table for model in read]

    def __enter__(self):
        self._exit_stack = transaction.atomic()
        self._exit_stack.__enter__()
        cursor = connection.cursor()
        for table in self.write_tables + self.read_tables:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [hash(table) % (2**31)],
            )
        cursor.close()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._exit_stack.__exit__(exc_type, exc_val, exc_tb)

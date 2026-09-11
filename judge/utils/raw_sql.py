from django.db.models.sql.constants import INNER, LOUTER
from django.db.models.sql.datastructures import Join


class RawSQLJoin(Join):
    def __init__(self, subquery, subquery_params, parent_alias, table_alias, join_type, join_field, nullable,
                 filtered_relation=None):
        self.subquery_params = subquery_params
        super().__init__(subquery, parent_alias, table_alias, join_type, join_field, nullable, filtered_relation)

    def as_sql(self, compiler, connection):
        compiler.quote_cache[self.table_name] = '(%s)' % self.table_name
        sql, params = super().as_sql(compiler, connection)
        return sql, self.subquery_params + params


class FakeJoinField:
    def __init__(self, joining_columns, related_model):
        self.joining_columns = joining_columns
        self.related_model = related_model

    def get_joining_columns(self):
        return self.joining_columns

    def get_extra_restriction(self, alias, remote_alias):
        pass


def join_sql_subquery(
        queryset, subquery, params, join_fields, alias, related_model, join_type=INNER, parent_model=None):
    if parent_model is not None:
        parent_alias = parent_model._meta.db_table
    else:
        parent_alias = queryset.query.get_initial_alias()
    if isinstance(queryset.query.external_aliases, dict):
        queryset.query.external_aliases[alias] = True
    else:
        queryset.query.external_aliases.add(alias)
    join = RawSQLJoin(subquery, params, parent_alias, alias, join_type, FakeJoinField(join_fields, related_model),
                      join_type == LOUTER)
    queryset.query.join(join)
    join.table_alias = alias


def use_straight_join(queryset):
    # No-op: STRAIGHT_JOIN is MySQL-specific and not needed on PostgreSQL
    pass

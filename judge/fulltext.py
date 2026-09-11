from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import models
from django.db.models import F
from django.db.models.query import QuerySet


class SearchQuerySet(QuerySet):
    DEFAULT = ''
    BOOLEAN = ' IN BOOLEAN MODE'
    NATURAL_LANGUAGE = ' IN NATURAL LANGUAGE MODE'
    QUERY_EXPANSION = ' WITH QUERY EXPANSION'

    def __init__(self, fields=None, **kwargs):
        super(SearchQuerySet, self).__init__(**kwargs)
        self._search_fields = fields

    def _clone(self, *args, **kwargs):
        queryset = super(SearchQuerySet, self)._clone(*args, **kwargs)
        queryset._search_fields = self._search_fields
        return queryset

    def search(self, query, mode=DEFAULT):
        search_query = SearchQuery(query)
        return self.annotate(
            relevance=SearchRank(F('search_vector'), search_query),
        ).filter(search_vector=search_query)


class SearchManager(models.Manager):
    def __init__(self, fields=None):
        super(SearchManager, self).__init__()
        self._search_fields = fields

    def get_queryset(self):
        if self._search_fields is not None:
            return SearchQuerySet(model=self.model, fields=self._search_fields)
        return super(SearchManager, self).get_queryset()

    def search(self, *args, **kwargs):
        return self.get_queryset().search(*args, **kwargs)

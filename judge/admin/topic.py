from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from reversion.admin import VersionAdmin

from judge.admin.utils import AdminFastPaginationMixin
from judge.models import Topic, TopicProblem
from judge.utils.views import NoBatchDeleteMixin


class TopicProblemInline(admin.TabularInline):
    model = TopicProblem
    extra = 1
    fields = ('problem', 'order')
    autocomplete_fields = ['problem']


class TopicAdmin(AdminFastPaginationMixin, VersionAdmin, NoBatchDeleteMixin):
    list_display = ('name', 'slug', 'order', 'problem_count', 'total_points', 'is_public', 'created_at')
    list_filter = ('is_public',)
    search_fields = ('name', 'slug', 'description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [TopicProblemInline]
    fields = ('name', 'slug', 'icon', 'order', 'description', 'is_public')

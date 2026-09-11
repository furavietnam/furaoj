from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from reversion.admin import VersionAdmin

from judge.admin.utils import AdminFastPaginationMixin
from judge.models import Exam, ExamCategory, ExamProblem, ExamProvince
from judge.utils.views import NoBatchDeleteMixin


class ExamProblemInline(admin.TabularInline):
    model = ExamProblem
    extra = 1
    fields = ('problem', 'order')
    autocomplete_fields = ['problem']


class ExamCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'full_name', 'order')
    ordering = ('order',)


class ExamProvinceAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'order')
    ordering = ('order',)


class ExamAdmin(AdminFastPaginationMixin, VersionAdmin, NoBatchDeleteMixin):
    list_display = ('name', 'category', 'province', 'year', 'problem_count', 'total_points', 'is_public')
    list_filter = ('is_public', 'category', 'province', 'year')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ExamProblemInline]
    fields = ('name', 'slug', 'category', 'province', 'year', 'date', 'description', 'is_public')

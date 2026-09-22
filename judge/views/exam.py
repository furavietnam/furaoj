from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import FloatField, Q, Max
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView, DetailView
from django.views.generic.edit import CreateView, UpdateView
from reversion import revisions

from judge.forms import ExamForm, ExamProblemFormSet
from judge.models import Exam, ExamCategory, ExamProvince, ExamProblem, Submission
from judge.utils.infinite_paginator import InfinitePaginationMixin
from judge.utils.views import TitleMixin


class ExamList(TitleMixin, InfinitePaginationMixin, ListView):
    model = Exam
    title = _('Exam Library')
    context_object_name = 'exams'
    template_name = 'exam/list.html'
    paginate_by = 20

    def get_queryset(self):
        queryset = Exam.objects.filter(is_public=True).select_related('category', 'province')

        q = self.request.GET.get('q', '').strip()
        if q:
            queryset = queryset.filter(
                Q(name__icontains=q) | Q(description__icontains=q)
            )

        category_id = self.request.GET.get('exam_category')
        if category_id:
            queryset = queryset.filter(category_id=category_id)

        province_id = self.request.GET.get('province')
        if province_id:
            queryset = queryset.filter(province_id=province_id)

        year = self.request.GET.get('year')
        if year:
            queryset = queryset.filter(year=year)

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = ExamCategory.objects.all()
        context['provinces'] = ExamProvince.objects.all()
        context['years'] = (
            Exam.objects.filter(is_public=True)
            .values_list('year', flat=True)
            .distinct()
            .order_by('-year')
        )
        context['search_query'] = self.request.GET.get('q', '')
        context['selected_category'] = self.request.GET.get('exam_category', '')
        context['selected_province'] = self.request.GET.get('province', '')
        context['selected_year'] = self.request.GET.get('year', '')

        if self.request.user.is_authenticated:
            profile = self.request.profile
            for exam in context['exams']:
                exam_problems = list(exam.exam_problems.select_related('problem'))
                problems = [ep.problem for ep in exam_problems]
                submissions = (
                    Submission.objects.filter(
                        user=profile,
                        problem__in=problems,
                        points__isnull=False,
                    )
                    .values('problem_id')
                    .annotate(best_points=Max('points', output_field=FloatField()))
                )
                problem_scores = {s['problem_id']: s['best_points'] for s in submissions}
                calc_total = sum(ep.problem.points or 0 for ep in exam_problems)
                exam.display_total = calc_total if calc_total > 0 else (exam.total_points or 0)
                exam.user_earned = sum(min(problem_scores.get(ep.problem.id, 0), ep.problem.points or 0) for ep in exam_problems)
                exam.solved_count = sum(1 for ep in exam_problems if problem_scores.get(ep.problem.id, 0) >= (ep.problem.points or 0) and (ep.problem.points or 0) > 0)
                exam.total_count = len(exam_problems)
                if exam.display_total > 0:
                    exam.progress_percent = min(100, int(round(exam.user_earned / exam.display_total * 100)))
                else:
                    exam.progress_percent = 0
        else:
            for exam in context['exams']:
                exam_problems = list(exam.exam_problems.select_related('problem'))
                calc_total = sum(ep.problem.points or 0 for ep in exam_problems)
                exam.user_earned = 0
                exam.display_total = calc_total if calc_total > 0 else (exam.total_points or 0)
                exam.solved_count = 0
                exam.total_count = len(exam_problems)
                exam.progress_percent = 0

        return context


class ExamDetail(TitleMixin, DetailView):
    model = Exam
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    context_object_name = 'exam'
    template_name = 'exam/detail.html'

    def get_title(self):
        return self.object.name

    def get_queryset(self):
        return Exam.objects.filter(is_public=True).select_related('category', 'province')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        exam = self.object

        exam_problems = list(ExamProblem.objects.filter(exam=exam).select_related('problem').order_by('order'))

        if self.request.user.is_authenticated:
            profile = self.request.profile
            problem_scores = {}
            submissions = (
                Submission.objects.filter(
                    user=profile,
                    problem__in=[ep.problem for ep in exam_problems],
                    points__isnull=False,
                )
                .values('problem_id')
                .annotate(best_points=Max('points', output_field=FloatField()))
            )
            for sub in submissions:
                problem_scores[sub['problem_id']] = sub['best_points']

            for ep in exam_problems:
                ep.user_score = problem_scores.get(ep.problem.id, 0)
        else:
            for ep in exam_problems:
                ep.user_score = 0

        context['exam_problems'] = exam_problems
        calc_total = sum(ep.problem.points or 0 for ep in exam_problems)
        exam.display_total = calc_total if calc_total > 0 else (exam.total_points or 0)

        if self.request.user.is_authenticated:
            context['user_total_score'] = sum(ep.user_score for ep in exam_problems)
            if exam.display_total > 0:
                context['progress_percent'] = min(100, int(round(context['user_total_score'] / exam.display_total * 100)))
            else:
                context['progress_percent'] = 0
            context['solved_count'] = sum(1 for ep in exam_problems if ep.user_score >= (ep.problem.points or 0) and (ep.problem.points or 0) > 0)
        else:
            context['user_total_score'] = 0
            context['progress_percent'] = 0
            context['solved_count'] = 0

        return context


class ExamCreate(PermissionRequiredMixin, TitleMixin, CreateView):
    template_name = 'exam/edit.html'
    model = Exam
    form_class = ExamForm
    permission_required = 'judge.add_exam'
    permission_denied_message = _('You are not allowed to create exams.')

    def get_title(self):
        return _('Create new exam')

    def get_content_title(self):
        return _('Create new exam')

    def get_exam_problem_formset(self):
        if self.request.POST:
            return ExamProblemFormSet(self.request.POST)
        return ExamProblemFormSet()

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        data['exam_problem_formset'] = self.get_exam_problem_formset()
        return data

    def post(self, request, *args, **kwargs):
        self.object = None
        form = ExamForm(request.POST)
        form_set = self.get_exam_problem_formset()
        if form.is_valid() and form_set.is_valid():
            with revisions.create_revision(atomic=True):
                self.object = form.save()
                problems = form_set.save(commit=False)
                for problem in form_set.deleted_objects:
                    problem.delete()
                for problem in problems:
                    problem.exam = self.object
                    problem.save()
                self.object.update_stats()
                revisions.set_comment(_('Created on site'))
                revisions.set_user(request.user)
            return HttpResponseRedirect(self.get_success_url())
        else:
            return self.render_to_response(self.get_context_data(*args, **kwargs))


class ExamEdit(LoginRequiredMixin, TitleMixin, UpdateView):
    template_name = 'exam/edit.html'
    model = Exam
    form_class = ExamForm
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    def get_object(self, queryset=None):
        exam = super().get_object(queryset)
        if not exam.is_editable_by(self.request.user):
            raise PermissionDenied(_('You are not allowed to edit this exam.'))
        return exam

    def get_title(self):
        return _('Editing exam {0}').format(self.object.name)

    def get_content_title(self):
        from django.utils.html import format_html
        from django.utils.safestring import mark_safe
        from django.utils.html import escape as html_escape
        return mark_safe(html_escape(_('Editing exam %s')) % (
            format_html('<a href="{1}">{0}</a>', self.object.name,
                        self.object.get_absolute_url())))

    def get_exam_problem_formset(self):
        if self.request.POST:
            return ExamProblemFormSet(self.request.POST, instance=self.object)
        return ExamProblemFormSet(instance=self.object)

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        data['exam_problem_formset'] = self.get_exam_problem_formset()
        return data

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        form_set = self.get_exam_problem_formset()

        if form.is_valid() and form_set.is_valid():
            with revisions.create_revision(atomic=True):
                form.save()
                problems = form_set.save(commit=False)
                for problem in form_set.deleted_objects:
                    problem.delete()
                for problem in problems:
                    problem.exam = self.object
                    problem.save()
                self.object.update_stats()
                revisions.set_comment(_('Edited from site'))
                revisions.set_user(request.user)
            return HttpResponseRedirect(self.get_success_url())
        else:
            return self.render_to_response(self.get_context_data(object=self.object))

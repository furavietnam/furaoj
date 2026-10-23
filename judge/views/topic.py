from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import FloatField, Max, Q
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, ListView

from judge.models import Problem, Submission, Topic, TopicProblem
from judge.utils.infinite_paginator import InfinitePaginationMixin
from judge.utils.views import TitleMixin


class TopicList(TitleMixin, InfinitePaginationMixin, ListView):
    model = Topic
    title = _('Topics')
    context_object_name = 'topics'
    template_name = 'topic/list.html'
    paginate_by = 24

    def get_queryset(self):
        if self.request.user.is_authenticated and (self.request.user.is_staff or self.request.user.is_superuser):
            queryset = Topic.objects.all()
        else:
            queryset = Topic.objects.filter(is_public=True)

        q = self.request.GET.get('q', '').strip()
        if q:
            queryset = queryset.filter(Q(name__icontains=q) | Q(description__icontains=q))

        return queryset.order_by('order', 'id').distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        topics = list(context['topics'])
        context['search_query'] = self.request.GET.get('q', '')

        if not topics:
            return context

        if self.request.user.is_authenticated and hasattr(self.request, 'profile'):
            profile = self.request.profile
            all_topic_problems = list(
                TopicProblem.objects.filter(topic__in=topics)
                .select_related('problem')
                .order_by('order', 'id')
            )
            problems = [tp.problem for tp in all_topic_problems]
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

            topic_problems_map = {}
            for tp in all_topic_problems:
                topic_problems_map.setdefault(tp.topic_id, []).append(tp)

            for topic in topics:
                tp_list = topic_problems_map.get(topic.id, [])
                calc_total = sum(tp.problem.points or 0 for tp in tp_list)
                topic.display_total = calc_total if calc_total > 0 else (topic.total_points or 0)
                topic.user_earned = sum(
                    min(problem_scores.get(tp.problem.id, 0), tp.problem.points or 0)
                    for tp in tp_list
                )
                topic.solved_count = sum(
                    1 for tp in tp_list
                    if problem_scores.get(tp.problem.id, 0) >= (tp.problem.points or 0) and (tp.problem.points or 0) > 0
                )
                topic.total_count = len(tp_list)
                if topic.display_total > 0:
                    topic.progress_percent = min(100, int(round(topic.user_earned / topic.display_total * 100)))
                else:
                    topic.progress_percent = 0
        else:
            for topic in topics:
                topic.user_earned = 0
                topic.solved_count = 0
                topic.total_count = topic.problem_count
                topic.display_total = topic.total_points or 0
                topic.progress_percent = 0

        return context


class TopicDetail(TitleMixin, DetailView):
    model = Topic
    context_object_name = 'topic'
    template_name = 'topic/detail.html'

    def get_object(self, queryset=None):
        slug = self.kwargs.get('slug')
        if not slug:
            raise Http404(_('Topic not found.'))

        if self.request.user.is_authenticated and (self.request.user.is_staff or self.request.user.is_superuser):
            qs = Topic.objects.all()
        else:
            qs = Topic.objects.filter(is_public=True)

        obj = qs.filter(slug=slug).first()
        if not obj:
            raise Http404(_('Topic not found.'))
        return obj

    def get_title(self):
        return self.object.name

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        topic = self.object

        topic_problems = list(
            TopicProblem.objects.filter(topic=topic)
            .select_related('problem')
            .order_by('order', 'id')
        )

        problem_scores = {}
        problem_status = {}
        if self.request.user.is_authenticated and hasattr(self.request, 'profile'):
            profile = self.request.profile
            submissions = (
                Submission.objects.filter(
                    user=profile,
                    problem__in=[tp.problem for tp in topic_problems],
                    points__isnull=False,
                )
                .values('problem_id')
                .annotate(best_points=Max('points', output_field=FloatField()))
            )
            for sub in submissions:
                problem_scores[sub['problem_id']] = sub['best_points']

            for tp in topic_problems:
                score = problem_scores.get(tp.problem.id)
                tp.user_score = score if score is not None else 0
                if score is not None:
                    max_pts = tp.problem.points or 0
                    if max_pts > 0 and score >= max_pts:
                        tp.status = 'AC'
                    elif score > 0:
                        tp.status = 'PAC'
                    else:
                        tp.status = 'WA'
                else:
                    tp.status = 'none'
        else:
            for tp in topic_problems:
                tp.user_score = 0
                tp.status = 'none'

        context['topic_problems'] = topic_problems
        calc_total = sum(tp.problem.points or 0 for tp in topic_problems)
        topic.display_total = calc_total if calc_total > 0 else (topic.total_points or 0)

        if self.request.user.is_authenticated and hasattr(self.request, 'profile'):
            context['user_total_score'] = sum(tp.user_score for tp in topic_problems)
            if topic.display_total > 0:
                context['progress_percent'] = min(
                    100,
                    int(round(context['user_total_score'] / topic.display_total * 100)),
                )
            else:
                context['progress_percent'] = 0
            context['solved_count'] = sum(
                1 for tp in topic_problems
                if tp.user_score >= (tp.problem.points or 0) and (tp.problem.points or 0) > 0
            )
        else:
            context['user_total_score'] = 0
            context['progress_percent'] = 0
            context['solved_count'] = 0

        context['can_edit'] = topic.is_editable_by(self.request.user)
        return context


class TopicAddProblem(View):
    def post(self, request, slug, *args, **kwargs):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse('auth_login'))

        if not (request.user.is_staff or request.user.is_superuser or request.user.has_perm('judge.change_topic')):
            raise PermissionDenied(_('You are not allowed to manage topic problems.'))

        topic = Topic.objects.filter(slug=slug).first()
        if not topic:
            raise Http404(_('Topic not found.'))

        problem_code = request.POST.get('problem_code', '').strip()
        if not problem_code:
            messages.error(request, _('Please enter a problem code.'))
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': _('Please enter a problem code.')}, status=400)
            return redirect(topic.get_absolute_url())

        problem = Problem.objects.filter(Q(code__iexact=problem_code) | Q(name__iexact=problem_code)).first()
        if not problem and problem_code.isdigit():
            problem = Problem.objects.filter(id=int(problem_code)).first()

        if not problem:
            messages.error(request, _('Problem "%(code)s" not found.') % {'code': problem_code})
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': _('Problem not found.')}, status=404)
            return redirect(topic.get_absolute_url())

        tp, created = TopicProblem.objects.get_or_create(
            topic=topic,
            problem=problem,
            defaults={'order': topic.topic_problems.count() + 1},
        )
        topic.update_stats()

        if created:
            messages.success(request, _('Added "%(name)s" (%(code)s) to topic.') % {'name': problem.name, 'code': problem.code})
        else:
            messages.info(request, _('Problem "%(code)s" is already in this topic.') % {'code': problem.code})

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'created': created,
                'problem_id': problem.id,
                'problem_code': problem.code,
                'problem_name': problem.name,
                'points': problem.points,
            })
        return redirect(topic.get_absolute_url())


class TopicRemoveProblem(View):
    def post(self, request, slug, problem_id, *args, **kwargs):
        if not request.user.is_authenticated:
            return HttpResponseRedirect(reverse('auth_login'))

        if not (request.user.is_staff or request.user.is_superuser or request.user.has_perm('judge.change_topic')):
            raise PermissionDenied(_('You are not allowed to manage topic problems.'))

        topic = Topic.objects.filter(slug=slug).first()
        if not topic:
            raise Http404(_('Topic not found.'))

        deleted_count, _counts = TopicProblem.objects.filter(topic=topic, problem_id=problem_id).delete()
        topic.update_stats()

        if deleted_count:
            messages.success(request, _('Removed problem from topic.'))

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return redirect(topic.get_absolute_url())

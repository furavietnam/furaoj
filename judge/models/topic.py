from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from judge.models.problem import Problem

__all__ = ['Topic', 'TopicProblem']


class Topic(models.Model):
    name = models.CharField(max_length=200, verbose_name=_('name'))
    slug = models.SlugField(max_length=200, verbose_name=_('slug'), unique=True)
    description = models.TextField(
        verbose_name=_('description'),
        blank=True,
        help_text=_('Markdown and LaTeX supported'),
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        default='fa-book',
        verbose_name=_('icon'),
        help_text=_('FontAwesome icon class, e.g. fa-book, fa-code, fa-laptop'),
    )
    order = models.IntegerField(default=0, verbose_name=_('order'))
    is_public = models.BooleanField(verbose_name=_('public'), default=True)
    problem_count = models.IntegerField(verbose_name=_('problem count'), default=0)
    total_points = models.FloatField(verbose_name=_('total points'), default=0)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('created at'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('updated at'))

    class Meta:
        verbose_name = _('topic')
        verbose_name_plural = _('topics')
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('topic_detail', args=(self.slug or self.id,))

    def is_editable_by(self, user):
        return bool(user.is_authenticated and (user.is_superuser or user.is_staff or user.has_perm('judge.change_topic')))

    def update_stats(self):
        stats = self.topic_problems.aggregate(
            count=models.Count('id'),
            total=models.Sum('problem__points'),
        )
        self.problem_count = stats['count'] or 0
        self.total_points = stats['total'] or 0
        Topic.objects.filter(pk=self.pk).update(
            problem_count=self.problem_count,
            total_points=self.total_points,
        )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.pk:
            self.update_stats()


class TopicProblem(models.Model):
    topic = models.ForeignKey(
        Topic,
        verbose_name=_('topic'),
        on_delete=models.CASCADE,
        related_name='topic_problems',
    )
    problem = models.ForeignKey(
        Problem,
        verbose_name=_('problem'),
        on_delete=models.CASCADE,
        related_name='topic_links',
    )
    order = models.IntegerField(verbose_name=_('order'), default=0)

    class Meta:
        verbose_name = _('topic problem')
        verbose_name_plural = _('topic problems')
        unique_together = ('topic', 'problem')
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.topic.name} - {self.problem.code}'

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import CASCADE, SET_NULL
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from judge.models.problem import Problem
from judge.models.profile import Profile

__all__ = ['ExamCategory', 'ExamProvince', 'Exam', 'ExamProblem']


class ExamCategory(models.Model):
    name = models.CharField(max_length=50, verbose_name=_('name'))
    full_name = models.CharField(max_length=100, verbose_name=_('full name'))
    order = models.IntegerField(default=0, verbose_name=_('order'))

    class Meta:
        verbose_name = _('exam category')
        verbose_name_plural = _('exam categories')
        ordering = ['order', 'name']

    def __str__(self):
        return self.full_name or self.name


class ExamProvince(models.Model):
    name = models.CharField(max_length=100, verbose_name=_('name'))
    slug = models.SlugField(verbose_name=_('slug'), unique=True)
    order = models.IntegerField(default=0, verbose_name=_('order'))

    class Meta:
        verbose_name = _('exam province')
        verbose_name_plural = _('exam provinces')
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Exam(models.Model):
    name = models.CharField(max_length=200, verbose_name=_('name'))
    slug = models.SlugField(verbose_name=_('slug'), unique=True)
    category = models.ForeignKey(ExamCategory, verbose_name=_('category'), null=True, blank=True,
                                 on_delete=SET_NULL, related_name='exams')
    province = models.ForeignKey(ExamProvince, verbose_name=_('province'), null=True, blank=True,
                                 on_delete=SET_NULL, related_name='exams')
    year = models.IntegerField(verbose_name=_('year'))
    date = models.DateField(verbose_name=_('date'), null=True, blank=True)
    description = models.TextField(verbose_name=_('description'), blank=True,
                                   help_text=_('Markdown supported'))
    is_public = models.BooleanField(verbose_name=_('public'), default=True)
    problem_count = models.IntegerField(verbose_name=_('problem count'), default=0)
    total_points = models.FloatField(verbose_name=_('total points'), default=0)

    class Meta:
        verbose_name = _('exam')
        verbose_name_plural = _('exams')
        ordering = ['-year', 'name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('exam_detail', args=(self.slug,))

    def is_editable_by(self, user):
        return user.is_superuser or user.has_perm('judge.change_exam')

    def update_stats(self):
        stats = self.exam_problems.aggregate(
            count=models.Count('id'),
            total=models.Sum('problem__points'),
        )
        self.problem_count = stats['count'] or 0
        self.total_points = stats['total'] or 0
        Exam.objects.filter(pk=self.pk).update(
            problem_count=self.problem_count,
            total_points=self.total_points,
        )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.update_stats()


class ExamProblem(models.Model):
    exam = models.ForeignKey(Exam, verbose_name=_('exam'), on_delete=CASCADE,
                             related_name='exam_problems')
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), on_delete=CASCADE,
                                related_name='exam_links')
    order = models.IntegerField(verbose_name=_('order'), default=0)

    class Meta:
        verbose_name = _('exam problem')
        verbose_name_plural = _('exam problems')
        unique_together = ('exam', 'problem')
        ordering = ['order']

    def __str__(self):
        return f'{self.exam.name} - {self.problem.code}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.exam.update_stats()

    def delete(self, *args, **kwargs):
        exam = self.exam
        super().delete(*args, **kwargs)
        exam.update_stats()


from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


@receiver([post_save, post_delete], sender=Problem)
def update_exams_on_problem_change(sender, instance, **kwargs):
    for exam in Exam.objects.filter(exam_problems__problem=instance).distinct():
        exam.update_stats()


from django.db import migrations


def update_contest_format(apps, schema_editor):
    Contest = apps.get_model('judge', 'Contest')
    Contest.objects.filter(format_name='vnoj').update(format_name='furaoj')


def reverse_update(apps, schema_editor):
    Contest = apps.get_model('judge', 'Contest')
    Contest.objects.filter(format_name='furaoj').update(format_name='vnoj')


class Migration(migrations.Migration):
    dependencies = [
        ('judge', '0234_problem_archived_at'),
    ]

    operations = [
        migrations.RunPython(update_contest_format, reverse_update),
    ]

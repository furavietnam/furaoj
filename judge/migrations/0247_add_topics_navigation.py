from django.db import migrations


def add_topics_navbar(apps, schema_editor):
    try:
        from judge.models import NavigationBar
        if not NavigationBar.objects.filter(key='topics').exists():
            contests_nav = NavigationBar.objects.filter(key='contest').first()
            order = (contests_nav.order + 1) if contests_nav else 5
            NavigationBar.objects.create(
                key='topics',
                label='Topics',
                path='/topics/',
                regex=r'^/(topic|topics)',
                order=order,
            )
    except Exception:
        pass


def remove_topics_navbar(apps, schema_editor):
    try:
        from judge.models import NavigationBar
        NavigationBar.objects.filter(key='topics').delete()
    except Exception:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0246_topic_alter_profile_math_engine_topicproblem'),
    ]

    operations = [
        migrations.RunPython(add_topics_navbar, remove_topics_navbar),
    ]

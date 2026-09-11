from django.conf import settings
from django.db import migrations


def create_org_admin_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name=settings.GROUP_PERMISSION_FOR_ORG_ADMIN)


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0237_alter_problemeasteregg_tag'),
    ]

    operations = [
        migrations.RunPython(create_org_admin_group, migrations.RunPython.noop),
    ]

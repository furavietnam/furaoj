from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0089_submission_to_contest'),
    ]

    operations = [
        migrations.RunSQL("""
            UPDATE judge_contest
            SET is_private = FALSE, is_organization_private = TRUE
            WHERE is_private = TRUE
        """, """
            UPDATE judge_contest
            SET is_private = is_organization_private
        """),
    ]

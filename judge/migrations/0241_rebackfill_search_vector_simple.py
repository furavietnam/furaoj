# Re-backfill search_vector using 'simple' config (no stopwords)
from django.db import migrations


def rebackfill_search_vector(apps, schema_editor):
    """Re-backfill search_vector for ALL Problem records using 'simple' config."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            UPDATE judge_problem
            SET search_vector = to_tsvector('simple',
                coalesce(code, '') || ' ' ||
                coalesce(name, '') || ' ' ||
                coalesce(description, '')
            )
        """)


def reverse_rebackfill(apps, schema_editor):
    """Clear search_vector values."""
    Problem = apps.get_model('judge', 'Problem')
    Problem.objects.update(search_vector=None)


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0240_add_search_vector_field'),
    ]

    operations = [
        migrations.RunPython(rebackfill_search_vector, reverse_rebackfill),
    ]

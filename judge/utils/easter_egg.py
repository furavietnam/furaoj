from django.db import transaction

from judge.models import EasterEgg, ProblemEasterEgg


def save_problem_easter_eggs(problem, easter_egg_data):
    """Save Easter Egg configurations from form data.

    Creates, updates, or deletes ProblemEasterEgg entries based on the
    submitted form data dict: {tag_code: egg_id_or_empty_string}.
    """
    with transaction.atomic():
        # Get current mappings
        current_mappings = {
            pe.tag: pe for pe in ProblemEasterEgg.objects.filter(problem=problem)
        }

        # Process form data
        tags_to_save = {}
        for tag_code, tag_label in EasterEgg.EASTER_EGG_TAG_CHOICES:
            value = easter_egg_data.get(tag_code, '')

            # Parse the value: could be empty, 'random', or an egg ID
            if value and value != 'random':
                try:
                    egg_id = int(value)
                    egg = EasterEgg.objects.get(id=egg_id, is_active=True)
                    tags_to_save[tag_code] = egg
                except (ValueError, EasterEgg.DoesNotExist):
                    tags_to_save[tag_code] = None
            elif value == 'random':
                # For IN_PROGRESS tag, store a special marker
                # The actual random selection happens in judge_handler.py
                tags_to_save[tag_code] = None
            else:
                tags_to_save[tag_code] = None

        # Update or create ProblemEasterEgg entries
        for tag_code, egg in tags_to_save.items():
            if tag_code in current_mappings:
                pe = current_mappings[tag_code]
                if egg is None:
                    # Check if this was previously configured
                    if pe.easter_egg_id is not None:
                        pe.easter_egg = None
                        pe.save()
                else:
                    if pe.easter_egg_id != egg.id:
                        pe.easter_egg = egg
                        pe.save()
            else:
                # Create new entry if egg is not None
                if egg is not None:
                    ProblemEasterEgg.objects.create(
                        problem=problem,
                        tag=tag_code,
                        easter_egg=egg,
                    )

        # Delete entries that are no longer needed
        for tag_code, pe in current_mappings.items():
            if tag_code not in tags_to_save:
                pe.delete()

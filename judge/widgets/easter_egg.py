import json

from django import forms
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from judge.models import EasterEgg


class EasterEggMatrixWidget(forms.Widget):
    """Custom widget that renders the Easter Egg matrix for all tags.

    The value_from_datadict method MUST be on the widget (not the field)
    because Django calls widget.value_from_datadict, not field.value_from_datadict.
    """
    template_name = 'judge/widget/easteregg_matrix_frontend.html'

    def __init__(self, attrs=None, template_name=None):
        super().__init__(attrs)
        if template_name:
            self.template_name = template_name

    def render(self, name, value, attrs=None, renderer=None):
        if value is None:
            value = {}
        # Get all active Easter Eggs grouped by tag
        eggs_by_tag = {}
        for egg in EasterEgg.objects.filter(is_active=True):
            if egg.tag not in eggs_by_tag:
                eggs_by_tag[egg.tag] = []
            eggs_by_tag[egg.tag].append({'id': egg.id, 'title': egg.title})

        # Build tag rows
        tag_rows = []
        for tag_code, tag_label in EasterEgg.EASTER_EGG_TAG_CHOICES:
            available_eggs = eggs_by_tag.get(tag_code, [])
            current_egg_id = value.get(tag_code)
            has_random = tag_code != 'IN_PROGRESS' and len(available_eggs) > 1
            tag_rows.append({
                'code': tag_code,
                'label': tag_label,
                'eggs': available_eggs,
                'current_egg_id': current_egg_id,
                'has_random': has_random,
            })

        context = {
            'tag_rows': tag_rows,
            'eggs_json': json.dumps(eggs_by_tag),
            'field_name': name,
        }
        # Use render_to_string with using='django' to force Django template engine
        # This ensures {% load i18n %} and other Django tags work even when
        # the frontend uses Jinja2 as its primary template backend.
        html = render_to_string(self.template_name, context, using='django')
        return mark_safe(html)

    def value_from_datadict(self, data, files, name):
        """Extract Easter egg data from POST data.

        The widget renders fields with names like 'field_name[TAG_CODE]',
        so we need to parse these into a dictionary.
        """
        result = {}
        prefix = name + '['
        for key, value in data.items():
            if key.startswith(prefix) and key.endswith(']'):
                tag_code = key[len(prefix):-1]
                result[tag_code] = value
        return result


class EasterEggMatrixFormField(forms.Field):
    """Custom form field that handles Easter Egg matrix data."""
    widget = EasterEggMatrixWidget

    def to_python(self, value):
        """Parse the POST data into a dict of {tag_code: egg_id or ''}."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        return {}

    def clean(self, value):
        # value comes as a dict from the widget's value_from_datadict
        if value is None:
            return {}
        return value

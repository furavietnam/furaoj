import functools
from datetime import datetime as datetime_type, timezone

from django.template.defaultfilters import date, time
from django.templatetags.tz import localtime
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from . import registry


def localtime_wrapper(func):
    @functools.wraps(func)
    def wrapper(value, *args, **kwargs):
        if isinstance(value, datetime_type) and getattr(value, 'convert_to_local_time', True):
            value = localtime(value)
        return func(value, *args, **kwargs)

    return wrapper


registry.filter(localtime_wrapper(date))
registry.filter(localtime_wrapper(time))


@registry.function
def relative_time(time, **kwargs):
    abs_time = date(time, kwargs.get('format', _('N j, Y, g:i a')))
    return mark_safe(f'<span data-iso="{time.astimezone(timezone.utc).isoformat()}" class="time-with-rel"'
                     f' title="{escape(abs_time)}" data-format="{escape(kwargs.get("rel", _("{time}")))}">'
                     f'{escape(kwargs.get("abs", _("on {time}")).replace("{time}", abs_time))}</span>')

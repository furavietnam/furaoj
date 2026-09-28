from django.contrib.auth.models import AbstractUser
from django.db import models

from judge.models import Profile
from . import registry

DEFAULT_AVATAR_URL = '/static/icons/default-avatar.svg'


def resolve_avatar(user_or_email, size=80, default=None):
    if default:
        return DEFAULT_AVATAR_URL

    if isinstance(user_or_email, Profile):
        if user_or_email.mute:
            return DEFAULT_AVATAR_URL
        return user_or_email.avatar_url

    if isinstance(user_or_email, AbstractUser):
        try:
            profile = user_or_email.profile
            if profile.mute:
                return DEFAULT_AVATAR_URL
            return profile.avatar_url
        except Exception:
            return DEFAULT_AVATAR_URL

    if isinstance(user_or_email, str):
        target = user_or_email.strip()
        if not target:
            return DEFAULT_AVATAR_URL
        try:
            profile = Profile.objects.filter(
                models.Q(user__email__iexact=target) | models.Q(user__username__iexact=target)
            ).first()
            if profile:
                if profile.mute:
                    return DEFAULT_AVATAR_URL
                return profile.avatar_url
        except Exception:
            pass

    return DEFAULT_AVATAR_URL


@registry.function
def avatar(user_or_email, size=80, default=None):
    return resolve_avatar(user_or_email, size=size, default=default)


@registry.function
def gravatar(user_or_email, size=80, default=None):
    return resolve_avatar(user_or_email, size=size, default=default)

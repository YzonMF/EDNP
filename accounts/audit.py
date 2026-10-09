from .models import AccountAuditLog

SERVER_ACTOR = 'Server command'


def display_name(user):
    """How a person is shown in the log. Clergy are known by their record's name
    (their user has none); registrar staff by the name on their account."""
    profile = getattr(user, 'clergy_profile', None)
    if profile is not None and profile.record is not None:
        return profile.record.name
    return user.get_full_name().strip() or user.username


def record(actor, target, action, note='', target_name=None):
    """Writes one audit entry. `actor` is the signed-in user doing it, or None
    for something run from the server command line. `target_name` overrides how
    the person is shown (clergy accounts are named by their record, not their
    username)."""
    return AccountAuditLog.objects.create(
        actor=actor, actor_name=display_name(actor) if actor else SERVER_ACTOR,
        target=target, target_name=target_name or display_name(target),
        action=action, note=(note or '').strip()[:255],
    )

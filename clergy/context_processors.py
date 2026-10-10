from .notifications import unread_count


def document_stats(request):
    """The clergy member's own submitted/total document counts, for the
    sidebar's My Documents nav badge."""
    if not request.user.is_authenticated:
        return {}
    profile = getattr(request.user, 'clergy_profile', None)
    if not profile or not profile.record:
        return {}
    summary = profile.record.document_summary()
    return {'nav_documents_submitted': summary['submitted'], 'nav_documents_total': summary['total']}


def notification_stats(request):
    """How many of the clergy member's notifications are unread, for the badge on the
    sidebar's bell. Registrar (staff) accounts have none."""
    if not request.user.is_authenticated or request.user.is_staff:
        return {}
    return {'nav_notifications_unread': unread_count(request.user)}

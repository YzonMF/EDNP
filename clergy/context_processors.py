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

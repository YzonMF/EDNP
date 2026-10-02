from .models import Document


def document_stats(request):
    """Total documents on file across every clergy record, for the sidebar's
    Documents nav badge. Only computed for logged-in registrar staff so
    anonymous/login-page renders don't touch the database."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return {}
    return {'nav_documents_count': Document.objects.count()}

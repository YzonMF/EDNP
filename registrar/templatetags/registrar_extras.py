from django import template

register = template.Library()

STATUS_BADGE_CLASSES = {
    'Active': 'active-b',
    'On Leave': 'pending-b',
    'Missionary to Other Diocese': 'abroad-b',
    'Retired': 'retired-b',
}


@register.filter
def status_badge_class(status):
    return STATUS_BADGE_CLASSES.get(status, 'pending-b')


@register.filter
def document_badge_class(summary):
    if not summary or summary['total'] == 0:
        return 'retired-b'
    if summary['expired']:
        return 'inactive-b'
    if summary['missing']:
        return 'pending-b'
    return 'active-b'

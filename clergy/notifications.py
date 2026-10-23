"""What the clergy portal's Notifications page shows.

Three things, kept apart because they behave differently:

- Events, stored as they happen (Notification rows): a sign-in from a new device,
  a changed password, username or number, a sign-in lockout, an edit to the
  profile or record, a document added, a reset done by the registrar. Each view
  that does one of these calls the matching function below, inside its own
  transaction where it has one, so the notice and the change stand or fall together.
- Reminders about dates on the record: a document about to expire or expired, the
  contract about to end or ended. There is no scheduler, so sync_reminders() makes
  them when the clergy member opens their dashboard (where every sign-in lands) or
  their notifications, and a key per reminder means each is only ever made once.
- attention(): what needs doing right now, worked out fresh on every view and never
  stored, so it can't go out of date.

Only clergy accounts get notifications; registrar staff have no page to read them on.
Never put a password, a texted code, a government ID number or a whole phone number in
one: the page shows them to whoever is signed in.
"""

from datetime import date, timedelta

from django.urls import reverse
from django.utils import dateformat, timezone
from django.utils.timesince import timesince

from accounts import devices
from accounts.audit import display_name
from accounts.phone import mask
from ednp import login_throttle

from .models import Notification

TITLE_MAX = Notification._meta.get_field('title').max_length
MESSAGE_MAX = Notification._meta.get_field('message').max_length

CONTRACT_REMINDER_DAYS = (7, 30, 90)   # one reminder as the end comes within each of these
DOCUMENT_REMINDER_DAYS = 30           # how long before a document expires to start warning
LOCKOUT_NOTICE_HOURS = 24             # at most one unread lockout notice per account in this long

# The official fields whose new value is spelled out when the registrar changes them:
# what a clergy member most needs to know, and on their record for them to see anyway.
# Any other field is named but its value left out (dates of birth, addresses and the
# like don't belong in a notification).
SPELLED_OUT_FIELDS = ('status', 'rank', 'deanery', 'assignment', 'contract_end', 'retirement_date')


def notify(user, kind, title, message='', dedupe_key=None):
    """Adds one notification for `user`. Does nothing for a registrar (staff) account, or no account."""
    if user is None or user.is_staff:
        return None
    return Notification.objects.create(user=user, kind=kind, title=_fit(title, TITLE_MAX),
                                       message=_fit(message, MESSAGE_MAX), dedupe_key=dedupe_key)


def _fit(text, limit):
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _day(value):
    return dateformat.format(value, 'M j, Y')


def _when(days):
    return {0: 'today', 1: 'tomorrow'}.get(days, f'in {days} days')


def _count(n, noun):
    return f'{n} {noun}{"" if n == 1 else "s"}'


def _registrar(user):
    return f'{display_name(user)} (Registrar)'


def _owner(record):
    """The portal account linked to a clergy record, or None if it has none."""
    profile = getattr(record, 'profile', None)
    return profile.user if profile is not None else None


# --- Sign-ins and account changes -------------------------------------------

def account_activated(user):
    notify(user, 'account_activated', 'Account activated',
           'Your mobile number is verified and your own password is set, so your account is ready to use.')


def new_sign_in(request, user, phone, remembered):
    """A sign-in from a browser that had to prove itself with a texted code. Sign-ins
    from trusted devices aren't notified: they're the everyday case."""
    device = devices.label_for(request)
    message = f'{device} signed in to your account with a code sent to {mask(phone)}.'
    if remembered:
        message += " It's now a trusted device and won't be asked for a code again."
    message += " If this wasn't you, change your password and remove the device under Security."
    notify(user, 'new_sign_in', f'New sign-in on {device}', message)


def password_changed(request, user):
    notify(user, 'password_changed', 'Password changed',
           f"Your password was changed on {devices.label_for(request)}. "
           "If this wasn't you, contact the Registrar right away.")


def password_reset(request, user):
    """After Forgot password, which also signs every other device out."""
    security = getattr(user, 'security', None)
    via = f' with a code sent to {mask(security.phone)}' if security is not None and security.phone else ''
    notify(user, 'password_reset', 'Password reset',
           f"Your password was reset on {devices.label_for(request)}{via}, and every other device was signed out. "
           "If this wasn't you, contact the Registrar right away.")


def username_changed(user, old, new):
    notify(user, 'username_changed', 'Username changed',
           f'Your username was changed from {old} to {new}. Use {new} the next time you sign in.')


def phone_changed(user, phone):
    notify(user, 'phone_changed', 'Mobile number changed',
           f'Your mobile number is now {mask(phone)}. Sign-in and password-reset codes go to it from now on, '
           'and your other trusted devices were removed.')


def device_removed(user, label):
    notify(user, 'device_removed', 'Trusted device removed',
           f'{label or "A browser"} is no longer trusted. It will be asked for a code the next time it signs in.')


def other_devices_removed(user, count):
    if count == 1:
        message = '1 other trusted device was removed. It will be asked for a code the next time it signs in.'
    else:
        message = f'{count} other trusted devices were removed. They will be asked for a code the next time they sign in.'
    notify(user, 'device_removed', 'Other devices removed', message)


def sign_in_locked(request, user):
    """Called on each wrong password at or past the lockout limit (from the sign-in page, or a
    security form asking for the current password). One unread notice a day is enough: someone
    hammering the account shouldn't bury everything else under copies of it."""
    if user is None or user.is_staff:
        return
    since = timezone.now() - timedelta(hours=LOCKOUT_NOTICE_HOURS)
    if Notification.objects.filter(user=user, kind='sign_in_locked', read_at__isnull=True, created_at__gte=since).exists():
        return
    notify(user, 'sign_in_locked', 'Sign-in paused after wrong passwords',
           f'Someone entered the wrong password for your account {login_throttle.MAX_ATTEMPTS} times, so signing in '
           f'was paused for {login_throttle.LOCKOUT_SECONDS // 60} minutes. The last try came from '
           f"{devices.label_for(request)}. If this wasn't you, change your password.")


def account_reset(user, action, registrar):
    """After the registrar's Reset & print slip; the owner reads it once they've set up again."""
    if action == 'lost_phone_reset':
        notify(user, 'account_reset', 'Account reset by the Registrar',
               f'{_registrar(registrar)} reset your account after a lost phone or number: your old number was '
               'removed, every device was signed out, and you were given a new temporary password.')
    else:
        notify(user, 'account_reset', 'New temporary password issued',
               f'{_registrar(registrar)} printed you a new temporary password.')


# --- Profile, record and documents ------------------------------------------

def profile_updated(user, forms):
    """After the clergy member saves My Record & Profile: names the fields that changed,
    never their values. Nothing if they saved without changing anything."""
    labels = [form.fields[name].label for form in forms for name in form.changed_data]
    if labels:
        notify(user, 'profile_updated', 'Profile updated', f'You updated: {", ".join(labels)}.')


def deployment_added(record, registrar, deployment):
    """After the registrar adds a deployment to a clergy record."""
    role_suffix = f' ({deployment.role})' if deployment.role else ''
    place_suffix = f' in {deployment.place_name}' if deployment.place_name else ''
    notify(_owner(record), 'deployment_added', 'Deployment added',
           f'{_registrar(registrar)} added a deployment for you at {deployment.station_name}{place_suffix}{role_suffix}.')


def deployment_deleted(record, registrar, station_name):
    """After the registrar deletes a deployment from a clergy record."""
    notify(_owner(record), 'deployment_removed', 'Deployment removed',
           f'{_registrar(registrar)} removed your deployment at {station_name}.')


def record_archived(record, registrar):
    """After the registrar archives a clergy record."""
    notify(_owner(record), 'record_archived', 'Record archived',
           f'{_registrar(registrar)} moved your clergy record to the archive.')


def record_restored(record, registrar):
    """After the registrar restores a clergy record from the archive."""
    notify(_owner(record), 'record_restored', 'Record restored',
           f'{_registrar(registrar)} restored your clergy record from the archive.')


def record_updated(record, registrar, form):
    """After the registrar saves a clergy member's record (call it after form.save())."""
    changes = [_describe(form, name) for name in form.changed_data]
    if changes:
        notify(_owner(record), 'record_updated', 'Your record was updated',
               f'{_registrar(registrar)} changed: {", ".join(changes)}.')


def _describe(form, name):
    label = form.fields[name].label
    if name not in SPELLED_OUT_FIELDS:
        return label
    value = getattr(form.instance, name)
    if value is None or value == '':
        return f'{label} (cleared)'
    if form.instance._meta.get_field(name).choices:
        value = getattr(form.instance, f'get_{name}_display')()
    elif isinstance(value, date):
        value = _day(value)
    return f'{label} (now {value})'


def document_uploaded(document, registrar=None):
    """After a document is saved to a record, by the registrar (`registrar` given) or by the clergy member."""
    name = document.document_type.name
    expiry = document.expiry_date()
    valid = f' It is valid until {_day(expiry)}.' if expiry else ''
    if registrar is not None:
        notify(_owner(document.record), 'document_uploaded', f'{name} added by the Registrar',
               f'{_registrar(registrar)} uploaded your {name}.{valid}')
    else:
        notify(_owner(document.record), 'document_uploaded', f'{name} uploaded',
               f'You uploaded your {name}.{valid}')


EMBER_COLLAPSE_MINUTES = 10


def ember_letter_uploaded(ember_letter, actor=None):
    """After an Ember letter is saved to a record. Collapses multiple uploads
    to the same record within EMBER_COLLAPSE_MINUTES into a single notification
    row so a 12-file batch doesn't flood the notifications page."""
    user = _owner(ember_letter.record)
    if user is None or user.is_staff:
        return None
    is_registrar = actor is not None and actor != user
    since = timezone.now() - timedelta(minutes=EMBER_COLLAPSE_MINUTES)
    recent_letter_count = ember_letter.record.ember_letters.filter(submitted_date__gte=since).count()
    recent_notice = (
        Notification.objects.filter(user=user, kind='ember_uploaded', created_at__gte=since)
        .order_by('-created_at')
        .first()
    )
    if recent_notice is not None and recent_letter_count > 1:
        if is_registrar:
            title = f'{recent_letter_count} Ember letters added by the Registrar'
            message = f'{_registrar(actor)} uploaded {recent_letter_count} Ember letters to your record.'
        else:
            title = f'{recent_letter_count} Ember letters uploaded'
            message = f'You uploaded {recent_letter_count} Ember letters.'
        recent_notice.title = _fit(title, TITLE_MAX)
        recent_notice.message = _fit(message, MESSAGE_MAX)
        recent_notice.read_at = None
        recent_notice.created_at = timezone.now()
        recent_notice.save(update_fields=['title', 'message', 'read_at', 'created_at'])
        return recent_notice

    slot = f'{ember_letter.year} Q{ember_letter.quarter}'
    if is_registrar:
        return notify(user, 'ember_uploaded', f'Ember Letter ({slot}) added by the Registrar',
                      f'{_registrar(actor)} uploaded your Ember Letter for {slot}.')
    return notify(user, 'ember_uploaded', f'Ember Letter ({slot}) uploaded',
                  f'You uploaded your Ember Letter for {slot}.')


def document_unmarked(record, registrar, doc_type):
    """After the registrar marks a noted requirement as Not Submitted."""
    notify(_owner(record), 'document_removed', f'{doc_type.name} marked not submitted',
           f'{_registrar(registrar)} marked your {doc_type.name} as Not Submitted.')


def ember_letter_deleted(record, registrar, slot):
    """After the registrar deletes an Ember letter from a record."""
    notify(_owner(record), 'document_removed', f'Ember Letter ({slot}) removed',
           f'{_registrar(registrar)} removed your Ember Letter for {slot}.')


# --- Reminders ----------------------------------------------------------------

def sync_reminders(user, record, today=None):
    """Makes each reminder that is due and hasn't been made yet. A reminder's key is what it
    is about plus the date in question, so a renewed document or a new contract date gets
    reminders of its own, and nothing is reminded twice. A few queries; cheap enough to run
    on every dashboard view."""
    if record is None or user.is_staff:
        return
    today = today or date.today()
    if record.contract_end and record.contract_end <= today:
        record.record_expired_contract(today=today)
    due = [*_contract_reminders(record, today), *_document_reminders(record, today)]
    if not due:
        return
    made = set(Notification.objects.filter(user=user, dedupe_key__in=[key for key, *_ in due])
               .values_list('dedupe_key', flat=True))
    Notification.objects.bulk_create([
        Notification(user=user, kind=kind, title=_fit(title, TITLE_MAX), message=_fit(message, MESSAGE_MAX), dedupe_key=key)
        for key, kind, title, message in due if key not in made
    ], ignore_conflicts=True)   # another tab making the same reminder at the same moment


def _contract_reminders(record, today):
    end = record.contract_end
    if end is None or record.status == 'Retired':
        return
    days = (end - today).days
    if days < 0:
        yield (f'contract:{end.isoformat()}:ended', 'contract_ended', 'Contract ended',
               f'Your contract ended on {_day(end)}. Please talk to the Registrar about what comes next.')
        return
    within = next((limit for limit in CONTRACT_REMINDER_DAYS if days <= limit), None)
    if within is not None:
        yield (f'contract:{end.isoformat()}:{within}', 'contract_reminder', 'Contract ending soon',
               f'Your contract ends {_when(days)}, on {_day(end)}. Please talk to the Registrar about renewal.')


def _document_reminders(record, today):
    for item in record.document_checklist():
        document = item['document']
        expiry = document.expiry_date() if document is not None else None
        if expiry is None:
            continue
        name = item['document_type'].name
        renew = ('The Registrar provides this document.' if item['document_type'].uploaded_by == 'registrar'
                 else 'Upload a renewed copy in My Requirements.')
        days = (expiry - today).days
        if days < 0:
            yield (f'document:{document.pk}:{expiry.isoformat()}:expired', 'document_expired', f'{name} expired',
                   f'Your {name} expired on {_day(expiry)}. {renew}')
        elif days <= DOCUMENT_REMINDER_DAYS:
            yield (f'document:{document.pk}:{expiry.isoformat()}:soon', 'document_expiring', f'{name} expires soon',
                   f'Your {name} expires {_when(days)}, on {_day(expiry)}. {renew}')


# --- What needs attention now ---------------------------------------------------

def attention(record, today=None):
    """What needs the clergy member's attention right now, as dicts for the page. Worked out
    from the record on every view, like the document checklist, and never stored. `record`
    is None for an account the registrar hasn't linked to a record yet."""
    if record is None:
        return [_item('warning', 'fa-link-slash',
                      'Your account is not linked to an official clergy record yet. Ask the Registrar to link one.')]
    today = today or date.today()
    requirements = reverse('clergy:my_requirements')
    checklist = record.document_checklist()
    items = []

    expired = [c['document_type'].name for c in checklist if c['status'] == 'Expired']
    if expired:
        items.append(_item('danger', 'fa-file-circle-exclamation',
                           f'{_count(len(expired), "document")} expired: {", ".join(expired)}.',
                           requirements, 'Open My Requirements'))
    expiring = [(c['document_type'].name, c['document'].expiry_date()) for c in checklist if c['status'] == 'Submitted']
    expiring = [(name, expiry) for name, expiry in expiring
                if expiry is not None and (expiry - today).days <= DOCUMENT_REMINDER_DAYS]
    if expiring:
        items.append(_item('warning', 'fa-hourglass-half',
                           'Expiring soon: ' + ', '.join(f'{name} ({_day(expiry)})' for name, expiry in expiring) + '.',
                           requirements, 'Open My Requirements'))
    to_submit = sum(1 for c in checklist if c['status'] == 'Missing' and c['document_type'].uploaded_by == 'clergy')
    if to_submit:
        items.append(_item('warning', 'fa-folder-open', f'{_count(to_submit, "required document")} still to submit.',
                           requirements, 'Open My Requirements'))

    end = record.contract_end
    if end is not None and record.status != 'Retired':
        days = (end - today).days
        if days < 0:
            items.append(_item('danger', 'fa-calendar-xmark',
                               f'Your contract ended on {_day(end)}. Please talk to the Registrar.'))
        elif days <= max(CONTRACT_REMINDER_DAYS):
            items.append(_item('warning', 'fa-calendar-days', f'Your contract ends {_when(days)}, on {_day(end)}.'))
    return items


def _item(tone, icon, text, link=None, link_text=''):
    return {'tone': tone, 'icon': icon, 'text': text, 'link': link, 'link_text': link_text}


# --- Reading them ---------------------------------------------------------------

def unread_count(user):
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_read(notifications):
    """Marks the given (already fetched) notifications as read. The objects keep their old
    read_at, so the page can still show which were new this once."""
    ids = [n.pk for n in notifications if n.read_at is None]
    if ids:
        Notification.objects.filter(pk__in=ids, read_at__isnull=True).update(read_at=timezone.now())


def mark_all_read(user):
    Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=timezone.now())


def ago(moment, now=None):
    """'Just now', '5 minutes ago', '3 days ago'. Relative, so it reads right in every time zone."""
    now = now or timezone.now()
    if now - moment < timedelta(minutes=1):
        return 'Just now'
    return f'{timesince(moment, now, depth=1)} ago'

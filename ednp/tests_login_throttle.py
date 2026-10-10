"""The sign-in lockout (ednp/login_throttle.py): what it counts, that the count is
shared and lasting, that it looks the same for names that exist and names that don't,
and what it writes to the security log."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError, OperationalError
from django.db.models.query import QuerySet
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import LoginThrottle
from accounts.testing import activate

from . import login_throttle

User = get_user_model()
PASSWORD = 'Correct-Horse-9'
WINDOW = login_throttle.LOCKOUT_SECONDS


def sign_in(username, password='wrong-guess'):
    return Client().post('/', {'username': username, 'password': password})


def locked(response):
    return b'Too many failed attempts' in response.content


def age_everything(seconds):
    LoginThrottle.objects.update(updated_at=timezone.now() - timedelta(seconds=seconds))


class LockoutTests(TestCase):
    def setUp(self):
        activate(User.objects.create_user('CLG-001', password=PASSWORD))

    def test_five_wrong_passwords_lock_the_account_even_against_the_right_one(self):
        for _ in range(5):
            self.assertContains(sign_in('CLG-001'), 'Invalid username or password')
        self.assertTrue(locked(sign_in('CLG-001', PASSWORD)))

    def test_four_wrong_passwords_do_not_lock_it(self):
        for _ in range(4):
            sign_in('CLG-001')
        self.assertEqual(sign_in('CLG-001', PASSWORD).status_code, 302)   # through to the next step

    def test_a_right_password_clears_the_count(self):
        for _ in range(4):
            sign_in('CLG-001')
        sign_in('CLG-001', PASSWORD)
        for _ in range(4):
            sign_in('CLG-001')
        self.assertFalse(locked(sign_in('CLG-001', PASSWORD)))   # four since the last success, not eight

    def test_the_lock_ends_once_the_window_has_passed(self):
        for _ in range(5):
            sign_in('CLG-001')
        age_everything(WINDOW + 1)
        self.assertEqual(sign_in('CLG-001', PASSWORD).status_code, 302)

    def test_the_lock_holds_until_the_window_has_passed(self):
        for _ in range(5):
            sign_in('CLG-001')
        age_everything(WINDOW - 5)
        self.assertTrue(locked(sign_in('CLG-001', PASSWORD)))

    def test_a_failure_after_the_window_starts_a_fresh_count(self):
        for _ in range(4):
            sign_in('CLG-001')
        age_everything(WINDOW + 1)
        self.assertEqual(login_throttle.register_failure('CLG-001'), ('CLG-001', 1))
        self.assertEqual(LoginThrottle.objects.get().failures, 1)

    def test_attempts_made_while_locked_do_not_extend_the_lock(self):
        for _ in range(5):
            sign_in('CLG-001')
        row = LoginThrottle.objects.get()
        for _ in range(3):
            sign_in('CLG-001')
        again = LoginThrottle.objects.get()
        self.assertEqual((again.failures, again.updated_at), (row.failures, row.updated_at))


SPELLINGS_TO_TRY = ('CLG-001', 'clg-001', 'ĆLG-001', 'ÇLG-001', 'CĹG-001', 'CŁG-001', 'CLĜ-001', 'CLĞ-001', 'ĆĹG-001', 'ĈĽĠ-001')


class SpellingTests(TestCase):
    """MySQL matches usernames ignoring case and accents, so the lockout must too."""

    def setUp(self):
        activate(User.objects.create_user('CLG-001', password=PASSWORD))
        # Which of these the database really treats as this account depends on its collation
        # (MariaDB's general_ci, for one, keeps 'Ł' apart from 'L'), so ask it rather than assume.
        self.same = [name for name in SPELLINGS_TO_TRY if User.objects.filter(username=name).exists()]
        if len(self.same) < 6 or 'ĈĽĠ-001' not in self.same:
            self.skipTest("this database doesn't treat look-alike spellings of a username as the same name")

    def test_look_alike_spellings_share_one_count(self):
        for name in self.same[:5]:   # five spellings, one account
            sign_in(name)
        self.assertEqual(LoginThrottle.objects.count(), 1)
        self.assertEqual(LoginThrottle.objects.get().failures, 5)
        for name in self.same:
            self.assertTrue(locked(sign_in(name, PASSWORD)), name)

    def test_the_bypass_that_was_found_is_closed(self):
        # Before: lock the real name, then a look-alike with the RIGHT password still got through.
        for _ in range(5):
            sign_in('CLG-001')
        response = sign_in('ĈĽĠ-001', PASSWORD)
        self.assertTrue(locked(response))
        self.assertNotEqual(response.status_code, 302)

    def test_a_renamed_account_keeps_its_count(self):
        for _ in range(5):
            sign_in('CLG-001')
        User.objects.filter(username='CLG-001').update(username='fr.juan')
        self.assertTrue(locked(sign_in('fr.juan', PASSWORD)))


class UnknownNameTests(TestCase):
    def test_a_name_that_matches_no_account_locks_just_the_same(self):
        for _ in range(5):
            sign_in('nobody-here')
        self.assertTrue(locked(sign_in('nobody-here')))

    def test_spellings_of_an_unknown_name_share_one_count(self):
        for name in ('Nobody', 'nobody', 'Nóbody', 'ＮＯＢＯＤＹ', '  NOBODY  '):
            sign_in(name)
        self.assertEqual(LoginThrottle.objects.count(), 1)
        self.assertEqual(LoginThrottle.objects.get().failures, 5)

    def test_the_lockout_cannot_be_used_to_tell_real_names_from_unknown_ones(self):
        activate(User.objects.create_user('CLG-002', password=PASSWORD))
        real = [sign_in('CLG-002').context['login_error_message'] for _ in range(7)]
        fake = [sign_in('nobody-here').context['login_error_message'] for _ in range(7)]
        self.assertEqual(real, fake)
        self.assertEqual(real[-1], login_throttle.LOCKOUT_MESSAGE)

    def test_blank_names_are_ignored(self):
        self.assertEqual(login_throttle.register_failure('   '), (None, 0))
        self.assertFalse(login_throttle.is_locked_out(''))
        login_throttle.clear('')
        self.assertFalse(LoginThrottle.objects.exists())

    def test_an_enormous_name_is_handled(self):
        self.assertEqual(login_throttle.register_failure('a' * 200_000), (None, 1))


class SharedAndLastingTests(TestCase):
    def test_the_count_is_not_held_in_one_process_memory(self):
        # The old counter lived in the cache: each gunicorn worker had its own, and a restart wiped them.
        activate(User.objects.create_user('CLG-001', password=PASSWORD))
        for _ in range(5):
            sign_in('CLG-001')
        cache.clear()   # what another worker, or a restart, would not have
        self.assertTrue(locked(sign_in('CLG-001', PASSWORD)))

    def test_only_a_hash_is_stored_never_the_typed_name(self):
        sign_in('hunter2-typed-in-the-wrong-box')
        row = LoginThrottle.objects.get()
        self.assertEqual(len(row.key_hash), 64)
        self.assertNotIn('hunter2', row.key_hash)

    def test_spaces_around_a_name_do_not_matter(self):
        activate(User.objects.create_user('CLG-001', password=PASSWORD))
        keys = {login_throttle._resolve(name)[0] for name in ('CLG-001', ' CLG-001 ')}
        self.assertEqual(len(keys), 1)


class TidyingTests(TestCase):
    """Old counts are swept away so the table can't grow without end, but never in a way that
    can get in the way of a sign-in."""

    def setUp(self):
        cache.clear()   # opens the once-in-a-while gate

    def test_counts_nobody_has_added_to_for_a_long_time_are_swept_away(self):
        sign_in('someone-long-ago')
        age_everything(2 * WINDOW + 60)
        cache.clear()   # by the time anyone next fails, the gate has opened again
        sign_in('someone-new')
        self.assertEqual(LoginThrottle.objects.count(), 1)
        self.assertEqual(LoginThrottle.objects.get().failures, 1)

    def test_a_count_that_has_only_just_expired_is_left_alone(self):
        sign_in('someone-a-while-ago')
        age_everything(WINDOW + 60)
        cache.clear()
        sign_in('someone-new')
        self.assertEqual(LoginThrottle.objects.count(), 2)

    def test_the_sweep_runs_only_once_in_a_while(self):
        sign_in('first')                      # opens the gate (nothing old yet)
        age_everything(2 * WINDOW + 60)
        sign_in('second')                     # the gate is shut: 'first' is not swept
        self.assertEqual(LoginThrottle.objects.count(), 2)
        cache.clear()                         # a few minutes later
        sign_in('third')                      # the gate is open again: 'first' goes
        self.assertEqual(LoginThrottle.objects.count(), 2)   # 'second' and 'third'

    def test_a_failed_sweep_never_breaks_a_sign_in(self):
        activate(User.objects.create_user('CLG-001', password=PASSWORD))
        with mock.patch.object(login_throttle, '_sweep', side_effect=DatabaseError('boom')):
            with self.assertLogs('ednp.login_throttle', level='WARNING') as logs:
                response = sign_in('CLG-001')
        self.assertContains(response, 'Invalid username or password')
        self.assertEqual(LoginThrottle.objects.get().failures, 1)
        self.assertIn('Could not clear old sign-in counters', logs.output[0])


class DeadlockTests(TestCase):
    """MySQL sometimes abandons one of two statements that collide on locks, saying to try again.
    The counting steps are safe to repeat, so they are. (The errors are simulated: a real deadlock
    can't be produced on demand inside a test.)"""

    def failing_update(self, code, times):
        calls = []
        real_update = QuerySet.update

        def update(queryset, **kwargs):
            if queryset.model is LoginThrottle:
                calls.append(1)
                if len(calls) <= times:
                    raise OperationalError(code, 'Deadlock found when trying to get lock; try restarting transaction')
            return real_update(queryset, **kwargs)

        return update, calls

    def outside_a_transaction(self):
        # A real request runs in autocommit; a test runs inside a transaction, which is the one case retrying is wrong.
        return mock.patch.object(login_throttle, 'connection', mock.Mock(in_atomic_block=False))

    def test_a_guess_that_is_chosen_as_the_victim_is_counted_on_the_second_try(self):
        update, calls = self.failing_update(1213, times=1)
        with self.outside_a_transaction(), mock.patch.object(login_throttle.time, 'sleep'), \
                mock.patch.object(QuerySet, 'update', update):
            self.assertEqual(login_throttle.register_failure('nobody-here'), (None, 1))
        self.assertEqual(LoginThrottle.objects.get().failures, 1)   # once, not twice

    def test_it_gives_up_after_three_attempts(self):
        update, calls = self.failing_update(1213, times=99)
        with self.outside_a_transaction(), mock.patch.object(login_throttle.time, 'sleep'), \
                mock.patch.object(QuerySet, 'update', update):
            with self.assertRaises(OperationalError):
                login_throttle.register_failure('nobody-here')
        self.assertEqual(len(calls), 3)

    def test_other_database_errors_are_not_retried(self):
        update, calls = self.failing_update(2006, times=99)   # "server has gone away"
        with self.outside_a_transaction(), mock.patch.object(QuerySet, 'update', update):
            with self.assertRaises(OperationalError):
                login_throttle.register_failure('nobody-here')
        self.assertEqual(len(calls), 1)

    def test_inside_a_larger_transaction_the_error_is_passed_on(self):
        update, calls = self.failing_update(1213, times=1)
        with mock.patch.object(QuerySet, 'update', update):   # the real connection: this test runs in a transaction
            with self.assertRaises(OperationalError):
                login_throttle.register_failure('nobody-here')
        self.assertEqual(len(calls), 1)


class RaceTests(TestCase):
    """Two requests for the same name can reach the database at the same moment. The count goes up
    in single atomic UPDATEs (so none is lost); the one place they can collide is both trying to
    create the row. That branch is reproduced here deterministically, with no threads: real
    threads are timing-dependent and, left in the suite, upset the other concurrency test."""

    def test_a_guess_that_loses_the_race_to_create_the_row_is_still_counted(self):
        login_throttle.register_failure('nobody-here')   # the row exists...
        real_update = QuerySet.update
        misses = []

        def blind_update(queryset, **kwargs):   # ...but our two UPDATEs ran before it appeared
            if queryset.model is LoginThrottle and len(misses) < 2:
                misses.append(1)
                return 0
            return real_update(queryset, **kwargs)

        with mock.patch.object(QuerySet, 'update', blind_update):
            self.assertEqual(login_throttle.register_failure('nobody-here'), (None, 2))
        self.assertEqual(LoginThrottle.objects.get().failures, 2)   # counted on top, not lost, no error


class LoggingTests(TestCase):
    def setUp(self):
        activate(User.objects.create_user('CLG-001', password=PASSWORD))

    def test_a_wrong_password_is_logged_without_the_password(self):
        with self.assertLogs('ednp.security', level='INFO') as logs:
            sign_in('CLG-001', 'my-secret-guess')
        self.assertEqual(len(logs.output), 1)
        self.assertIn('INFO:ednp.security:sign-in failed account=CLG-001 failures=1 from=127.0.0.1', logs.output[0])
        self.assertNotIn('my-secret-guess', logs.output[0])

    def test_the_fifth_wrong_password_is_logged_as_a_lockout(self):
        for _ in range(4):
            sign_in('CLG-001')
        with self.assertLogs('ednp.security', level='WARNING') as logs:
            sign_in('CLG-001')
        self.assertIn('WARNING:ednp.security:sign-in locked account=CLG-001 failures=5', logs.output[0])

    def test_a_name_that_matches_no_account_is_never_written_to_the_log(self):
        with self.assertLogs('ednp.security', level='INFO') as logs:
            sign_in('hunter2-typed-in-the-wrong-box')
        self.assertIn('account=-', logs.output[0])
        self.assertNotIn('hunter2', logs.output[0])

    def test_a_forged_forwarding_header_cannot_write_text_into_the_log(self):
        forged = '1.2.3.4, failures=0 account=CLG-999 sign-in locked'
        with self.assertLogs('ednp.security', level='INFO') as logs:
            Client().post('/', {'username': 'CLG-001', 'password': 'x'}, HTTP_X_FORWARDED_FOR=forged)
        self.assertIn('fwd=-', logs.output[0])
        self.assertNotIn('CLG-999', logs.output[0])

    def test_the_last_forwarded_address_is_shown_beside_the_connection(self):
        with self.assertLogs('ednp.security', level='INFO') as logs:
            Client().post('/', {'username': 'CLG-001', 'password': 'x'}, HTTP_X_FORWARDED_FOR='9.9.9.9, 8.8.4.4')
        self.assertIn('from=127.0.0.1 fwd=8.8.4.4', logs.output[0])

from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import CommandError, call_command
from django.test import TestCase
from django_otp.plugins.otp_totp.models import TOTPDevice

STRONG_PASSWORD = "Parola-PriceMatch-2026!"


class AccountCommandTests(TestCase):
    @patch("comparator.management.commands.create_staff_user.getpass.getpass")
    def test_create_staff_user_prompts_for_password_and_is_not_superuser(self, prompt):
        prompt.side_effect = [STRONG_PASSWORD, STRONG_PASSWORD]

        call_command("create_staff_user", "operator")

        user = get_user_model().objects.get(username="operator")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(STRONG_PASSWORD))

    @patch("comparator.management.commands.create_staff_user.getpass.getpass")
    def test_create_staff_user_rejects_mismatched_passwords(self, prompt):
        prompt.side_effect = [STRONG_PASSWORD, STRONG_PASSWORD + "x"]

        with self.assertRaisesMessage(CommandError, "Parolele nu coincid"):
            call_command("create_staff_user", "operator")

        self.assertFalse(get_user_model().objects.filter(username="operator").exists())

    def test_disable_revokes_sessions_but_protects_last_superuser(self):
        users = get_user_model()
        owner = users.objects.create_superuser("owner", password=STRONG_PASSWORD)
        operator = users.objects.create_user(
            "operator", password=STRONG_PASSWORD, is_staff=True
        )
        session = SessionStore()
        session["_auth_user_id"] = str(operator.pk)
        session.create()

        call_command("disable_staff_user", "operator")

        operator.refresh_from_db()
        self.assertFalse(operator.is_active)
        self.assertFalse(SessionStore(session_key=session.session_key).exists(session.session_key))
        with self.assertRaisesMessage(CommandError, "Ultimul superuser activ"):
            call_command("disable_staff_user", owner.username)

    def test_reset_mfa_requires_confirmation_and_removes_device(self):
        user = get_user_model().objects.create_user(
            "operator", password=STRONG_PASSWORD, is_staff=True
        )
        TOTPDevice.objects.create(user=user, name="Telefon", confirmed=True)

        with self.assertRaisesMessage(CommandError, "--confirm"):
            call_command("reset_staff_mfa", user.username)
        call_command("reset_staff_mfa", user.username, "--confirm")

        self.assertFalse(TOTPDevice.objects.filter(user=user).exists())

    def test_list_shows_access_and_mfa_without_email(self):
        user = get_user_model().objects.create_user(
            "operator",
            email="secret@example.com",
            password=STRONG_PASSWORD,
            is_staff=True,
        )
        TOTPDevice.objects.create(user=user, name="Telefon", confirmed=True)
        output = StringIO()

        call_command("list_staff_users", stdout=output)

        self.assertIn("operator\tda\toperator\tconfigurat", output.getvalue())
        self.assertNotIn("secret@example.com", output.getvalue())

    @patch("comparator.management.commands.create_staff_user.getpass.getpass")
    def test_create_staff_user_can_create_explicit_admin_role(self, prompt):
        prompt.side_effect = [STRONG_PASSWORD, STRONG_PASSWORD]

        call_command("create_staff_user", "owner", "--role", "admin")

        user = get_user_model().objects.get(username="owner")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_users_and_groups_are_not_manageable_from_web_admin(self):
        self.assertNotIn(get_user_model(), admin.site._registry)
        self.assertNotIn(Group, admin.site._registry)

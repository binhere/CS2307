"""The shared demo account: it must exist and accept its published password whenever someone uses it.

The login page offers a one-click demo sign-in, so the account is created on demand, restored when
its password or active flag changed, and its username can never be registered by someone else.
"""

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError

from src.api.v1.endpoints.auth import view_auth
from src.services.auth import demo_account
from src.services.auth.schemas import LoginRequest, RegisterRequest

DEMO = (demo_account.DEFAULT_DEMO_USERNAME, demo_account.DEFAULT_DEMO_PASSWORD)


class FakeUsers:
    """In-memory stand-in for the users repository, with plain-text "hashes" for speed."""

    def __init__(self, user=None, race=False):
        self.user, self.race = user, race
        self.created, self.restored = [], []

    def get_by_username(self, username):
        return self.user if self.user and self.user["username"] == username else None

    def create_user(self, username, password_hash, display_name):
        if self.race:
            self.user = {"id": 2, "username": username, "password_hash": "hashed:" + DEMO[1], "is_active": True}
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        self.created.append((username, password_hash, display_name))
        return {"id": 1, "username": username, "display_name": display_name}

    def restore_account(self, user_id, password_hash):
        self.restored.append((user_id, password_hash))


def patched(users, settings=None):
    """Patch the repository, cheap hashing and optionally the settings of demo_account."""
    patches = [
        patch.object(demo_account, "repository", users),
        patch.object(demo_account, "hash_password", lambda password: "hashed:" + password),
        patch.object(demo_account, "verify_password", lambda password, password_hash: password_hash == "hashed:" + password),
    ]
    if settings is not None:
        patches.append(patch.object(demo_account, "config_value", lambda name, default=None: settings.get(name, default)))
    return patches


class DemoAccountTests(unittest.TestCase):
    def run_with(self, users, settings=None):
        patches = patched(users, settings)
        for each in patches:
            each.start()
        try:
            return demo_account.ensure_demo_account()
        finally:
            for each in reversed(patches):
                each.stop()

    def test_a_missing_demo_account_is_created_with_the_published_password(self):
        users = FakeUsers()
        self.run_with(users)
        self.assertEqual(users.created, [(DEMO[0], "hashed:" + DEMO[1], demo_account.DEMO_DISPLAY_NAME)])

    def test_a_changed_password_or_disabled_account_is_restored(self):
        for user in ({"id": 5, "username": DEMO[0], "password_hash": "hashed:other", "is_active": True},
                     {"id": 5, "username": DEMO[0], "password_hash": "hashed:" + DEMO[1], "is_active": False}):
            with self.subTest(user=user):
                users = FakeUsers(user)
                self.run_with(users)
                self.assertEqual(users.restored, [(5, "hashed:" + DEMO[1])])

    def test_an_intact_account_is_left_alone(self):
        users = FakeUsers({"id": 5, "username": DEMO[0], "password_hash": "hashed:" + DEMO[1], "is_active": True})
        self.run_with(users)
        self.assertEqual((users.created, users.restored), ([], []))

    def test_a_concurrent_creation_uses_the_account_the_other_request_made(self):
        users = FakeUsers(race=True)
        self.assertEqual(self.run_with(users)["id"], 2)

    def test_an_empty_username_setting_disables_the_demo_account(self):
        users = FakeUsers()
        self.assertIsNone(self.run_with(users, {"DEMO_ACCOUNT_USERNAME": ""}))
        self.assertEqual(users.created, [])

    def test_startup_survives_an_unavailable_database(self):
        def unavailable():
            raise OperationalError("SELECT", {}, Exception("down"))

        with patch.object(demo_account, "ensure_demo_account", unavailable):
            demo_account.ensure_demo_account_at_startup()


class DemoAccountViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_nobody_can_register_the_demo_username(self):
        with patch.object(view_auth.repository, "get_by_username", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                await view_auth.register(RegisterRequest(username=DEMO[0], password="mine"))
        self.assertEqual(raised.exception.status_code, 409)

    async def test_signing_in_as_demo_makes_sure_the_account_exists_first(self):
        calls = []
        user = {"id": 1, "username": DEMO[0], "password_hash": "hash", "display_name": "Tài khoản demo", "is_active": True}
        with patch.object(view_auth, "ensure_demo_account", lambda: calls.append("ensure")), \
                patch.object(view_auth.repository, "get_by_username", side_effect=lambda name: calls.append("read") or user), \
                patch.object(view_auth, "verify_password", return_value=True), \
                patch.object(view_auth, "create_access_token", return_value="token"):
            body = await view_auth.login(LoginRequest(username=DEMO[0], password=DEMO[1]))
        self.assertEqual(calls, ["ensure", "read"])
        self.assertEqual(body["data"]["access_token"], "token")


class FrontendInStepTests(unittest.TestCase):
    def test_the_sign_in_page_offers_the_credentials_the_backend_accepts(self):
        import re
        from pathlib import Path

        frontend_file = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "demo-account.ts"
        if not frontend_file.exists():  # backend-only checkouts, e.g. inside the image
            self.skipTest("frontend not present")
        source = frontend_file.read_text(encoding="utf-8")
        self.assertEqual(re.search(r'username: "([^"]+)"', source).group(1), DEMO[0])
        self.assertEqual(re.search(r'password: "([^"]+)"', source).group(1), DEMO[1])


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.api.v1.endpoints.auth import view_auth
from src.services.auth.schemas import LoginRequest, RegisterRequest
from src.services.auth.security import DUMMY_PASSWORD_HASH


class AuthViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_duplicate_registration_is_a_conflict(self):
        def duplicate(*_arguments):
            raise IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))

        with patch.object(view_auth.repository, "get_by_username", return_value=None), patch.object(
            view_auth.repository, "create_user", side_effect=duplicate
        ), patch.object(view_auth, "hash_password", return_value="hash"):
            with self.assertRaises(HTTPException) as context:
                await view_auth.register(RegisterRequest(username="lan", password="secret"))

        self.assertEqual(context.exception.status_code, 409)

    async def test_unknown_username_still_pays_for_a_password_check(self):
        with patch.object(view_auth.repository, "get_by_username", return_value=None), patch.object(
            view_auth, "verify_password", return_value=False
        ) as verify:
            with self.assertRaises(HTTPException) as context:
                await view_auth.login(LoginRequest(username="ghost", password="guess"))

        self.assertEqual(context.exception.status_code, 401)
        verify.assert_called_once_with("guess", DUMMY_PASSWORD_HASH)

    def test_dummy_hash_uses_the_same_bcrypt_cost_as_real_accounts(self):
        self.assertTrue(DUMMY_PASSWORD_HASH.startswith("$2b$12$"))


if __name__ == "__main__":
    unittest.main()

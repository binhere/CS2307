"""The shared demo account: a ready account anyone can use to try the site without signing up.

Its credentials are public on purpose. The login page offers a one-click button and shows them
(frontend/lib/demo-account.ts holds the same defaults). The account has no extra rights, and
everyone who uses it sees the same chat history, saved and posted listings. It is created when
the backend starts and again whenever someone signs in with its username, and its password and
active flag are put back if they were changed, so the button always works.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger
from sqlalchemy.exc import IntegrityError

from src.services.auth import repository
from src.services.auth.security import hash_password, verify_password
from src.settings.config import config_value

# Keep in step with frontend/lib/demo-account.ts.
DEFAULT_DEMO_USERNAME = "admin@gmail.com"
DEFAULT_DEMO_PASSWORD = "demo123456"
DEMO_DISPLAY_NAME = "Tài khoản demo"


def demo_credentials() -> Optional[Tuple[str, str]]:
    """The demo account's username and password.

    Returns:
        tuple | None: (username, password) from DEMO_ACCOUNT_USERNAME / DEMO_ACCOUNT_PASSWORD
        or the defaults; None when DEMO_ACCOUNT_USERNAME is set empty (demo account disabled).
    """
    username = str(config_value("DEMO_ACCOUNT_USERNAME", DEFAULT_DEMO_USERNAME)).strip()
    if not username:
        return None
    return username, str(config_value("DEMO_ACCOUNT_PASSWORD", DEFAULT_DEMO_PASSWORD))


def is_demo_username(username: str) -> bool:
    """Whether a username is the demo account's (reserved: nobody may register it)."""
    credentials = demo_credentials()
    return credentials is not None and username.strip() == credentials[0]


def ensure_demo_account() -> Optional[dict]:
    """Create the demo account, or restore its password and active flag.

    Blocking (database and bcrypt): call through run_in_threadpool from async code.

    Returns:
        dict | None: The account row, or None when the demo account is disabled.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: The database is unavailable.
    """
    credentials = demo_credentials()
    if credentials is None:
        return None
    username, password = credentials
    user = repository.get_by_username(username)
    if user is None:
        try:
            created = repository.create_user(username, hash_password(password), DEMO_DISPLAY_NAME)
            logger.info("Created the demo account {}", username)
            return created
        except IntegrityError:
            # Two workers or requests created it at the same moment; the other one won.
            user = repository.get_by_username(username)
            if user is None:
                raise
    if not user.get("is_active", True) or not verify_password(password, user["password_hash"]):
        repository.restore_account(user["id"], hash_password(password))
        logger.info("Restored the demo account {}", username)
    return user


def ensure_demo_account_at_startup() -> None:
    """Best-effort creation when the backend starts: the API must start even without the database."""
    try:
        ensure_demo_account()
    except Exception as error:
        logger.warning("Demo account not ensured at startup ({}); it is created at its first sign-in", type(error).__name__)

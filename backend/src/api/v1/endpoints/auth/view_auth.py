from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from src.services.auth import repository
from src.services.auth.demo_account import ensure_demo_account, is_demo_username
from src.services.auth.dependencies import get_current_user
from src.services.auth.schemas import (
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    UserPublic,
)
from src.services.auth.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    verify_password,
)


router = APIRouter()

_USERNAME_TAKEN = "Tên đăng nhập đã tồn tại"


def _auth_response(user_id: int, username: str, display_name) -> dict:
    token = create_access_token(user_id, username)
    payload = AuthResponse(
        access_token=token,
        user=UserPublic(id=user_id, username=username, display_name=display_name),
    )
    return {"data": payload.model_dump(), "errors": [], "status": "success"}


@router.post("/register", summary="Register a username/password account")
async def register(data: RegisterRequest):
    """Create an account and return a signed access token.

    Args:
        data: Username (1-64 chars), password (1-128 chars), optional display name.

    Returns:
        dict: {"data": {"access_token", "token_type", "user"}, "errors": [], "status": "success"}.

    Raises:
        HTTPException: 409 when the username is taken, including when a concurrent
        registration claims it between the existence check and the insert.
    """
    existing = await run_in_threadpool(repository.get_by_username, data.username)
    # The demo account's username is reserved even before the account exists.
    if existing or is_demo_username(data.username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_USERNAME_TAKEN)
    # bcrypt takes ~200 ms of CPU; hashing on the event loop would stall every
    # other request in this single-worker process.
    password_hash = await run_in_threadpool(hash_password, data.password)
    try:
        user = await run_in_threadpool(
            repository.create_user,
            data.username,
            password_hash,
            data.display_name,
        )
    except IntegrityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_USERNAME_TAKEN) from error
    return _auth_response(user["id"], user["username"], user.get("display_name"))


@router.post("/login", summary="Log in with username/password")
async def login(data: LoginRequest):
    """Check a username/password pair and return a signed access token.

    Args:
        data: Username and password.

    Returns:
        dict: {"data": {"access_token", "token_type", "user"}, "errors": [], "status": "success"}.

    Raises:
        HTTPException: 401 for an unknown username or wrong password (same
        message and similar timing for both), 403 for a disabled account.
    """
    if is_demo_username(data.username):
        # The shared demo account must always accept its published password.
        await run_in_threadpool(ensure_demo_account)
    user = await run_in_threadpool(repository.get_by_username, data.username)
    password_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    password_matches = await run_in_threadpool(verify_password, data.password, password_hash)
    if not user or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tên đăng nhập hoặc mật khẩu không đúng",
        )
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đã bị vô hiệu hóa",
        )
    return _auth_response(user["id"], user["username"], user.get("display_name"))


@router.get("/me", summary="Get the current authenticated user")
async def me(current_user: dict = Depends(get_current_user)):
    payload = UserPublic(
        id=current_user["id"],
        username=current_user["username"],
        display_name=current_user.get("display_name"),
    )
    return {"data": payload.model_dump(), "errors": [], "status": "success"}

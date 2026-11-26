// The shared demo account offered on the sign-in page, so anyone can try the signed-in features
// without registering. Its password is public on purpose. The backend creates the account and
// restores this password whenever it is used (backend/src/services/auth/demo_account.py, which
// holds the same defaults; a backend test keeps the two in step).

/** Username and password of the demo account. */
export const DEMO_ACCOUNT = {
  username: "admin@gmail.com",
  password: "demo123456",
} as const

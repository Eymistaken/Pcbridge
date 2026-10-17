"""Test-only stand-in for the PAM login check, for tests that run pcbridge in a subprocess.

Put this directory and the repository on PYTHONPATH and set
PCBRIDGE_TEST_LOGIN_PASSWORD: `loginpw.verify` then accepts exactly that
value. pcbridge itself has no such switch; nothing outside tests/ loads this.
"""

import os

if os.environ.get("PCBRIDGE_TEST_LOGIN_PASSWORD"):
    import pcbridge.loginpw as _loginpw

    _expected = os.environ["PCBRIDGE_TEST_LOGIN_PASSWORD"]
    _loginpw.verify = lambda password: bool(password) and password == _expected

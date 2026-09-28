import stat

from tabdeck.auth import Auth, is_local


def test_is_local():
    assert is_local("127.0.0.1") and is_local("::1")
    assert not is_local("192.0.2.21") and not is_local("")


def test_pairing_flow(tmp_path):
    f = tmp_path / "tokens.json"
    auth = Auth(f)
    code = auth.new_pairing_code(now=0)
    token = auth.redeem(code, now=10)
    assert token and auth.valid(token)
    assert auth.redeem(code, now=11) is None
    assert token not in f.read_text()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert Auth(f).valid(token)


def test_expired_code(tmp_path):
    auth = Auth(tmp_path / "t.json")
    code = auth.new_pairing_code(now=0)
    assert auth.redeem(code, now=601) is None


def test_invalid_and_revoke(tmp_path):
    auth = Auth(tmp_path / "t.json")
    assert not auth.valid(None) and not auth.valid("garbage")
    token = auth.redeem(auth.new_pairing_code(0), 1)
    auth.revoke_all()
    assert not auth.valid(token)

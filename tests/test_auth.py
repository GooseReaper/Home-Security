from homecontrol.auth import LoginLimiter, Sessions, hash_password, verify_password


def test_password_hash_roundtrip():
    stored = hash_password("s3cret-pass", iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$")
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret-pass", "garbage")


def test_sessions():
    sessions = Sessions(hours=1)
    token = sessions.create()
    assert sessions.valid(token)
    assert not sessions.valid("made-up")
    assert not sessions.valid(None)
    sessions.revoke(token)
    assert not sessions.valid(token)


def test_expired_session():
    sessions = Sessions(hours=-1)
    assert not sessions.valid(sessions.create())


def test_limiter_locks_after_five_failures():
    limiter = LoginLimiter()
    for _ in range(4):
        limiter.failed("1.2.3.4")
    assert not limiter.locked("1.2.3.4")
    limiter.failed("1.2.3.4")
    assert limiter.locked("1.2.3.4")
    assert not limiter.locked("5.6.7.8")

"""Clinic passwords are kept only as salted scrypt hashes."""
from app.core.passwords import generate_password, hash_password, verify_password


def test_the_right_password_matches():
    assert verify_password("open sesame", hash_password("open sesame"))


def test_a_wrong_password_does_not():
    assert not verify_password("open sesam", hash_password("open sesame"))


def test_the_password_itself_is_not_stored():
    assert "open sesame" not in hash_password("open sesame")


def test_the_same_password_hashes_differently_each_time():
    """A salt each, so two clinics with one password do not look alike."""
    assert hash_password("same") != hash_password("same")


def test_nonsense_never_matches_and_never_raises():
    for stored in ("", None, "plain", "scrypt$1$2$3$x$y", "bcrypt$a$b$c$d$e", "scrypt$16384$8$1$%%%$%%%"):
        assert verify_password("anything", stored) is False


def test_hebrew_and_symbols_work():
    assert verify_password("סיסמה-חזקה!", hash_password("סיסמה-חזקה!"))


def test_generated_passwords_are_long_and_different():
    first, second = generate_password(), generate_password()
    assert len(first) >= 12 and first != second

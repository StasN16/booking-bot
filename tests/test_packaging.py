"""
The lock file must install everything the bot needs on every machine it runs on.

SQLAlchemy lists greenlet, which its async engine runs on, only for the
machines it names, and an Apple Silicon Mac ("arm64") is not among them.
Locked that way, a new Mac installed without greenlet and every database
call failed with "the greenlet library is required".
"""
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = tomllib.loads((ROOT / "poetry.lock").read_text(encoding="utf-8"))


def locked(name: str) -> dict:
    return next(package for package in LOCK["package"] if package["name"] == name)


def test_greenlet_is_installed_on_every_machine():
    assert "markers" not in locked("greenlet")


def test_greenlet_has_a_wheel_for_apple_silicon():
    files = [entry["file"] for entry in locked("greenlet")["files"]]
    assert any("cp312" in f and "macosx" in f and ("universal2" in f or "arm64" in f) for f in files)


def test_the_async_engine_can_start():
    """What failed on the Mac: SQLAlchemy checks for greenlet when it is used."""
    from sqlalchemy.util import greenlet_spawn  # noqa: F401
    import greenlet  # noqa: F401

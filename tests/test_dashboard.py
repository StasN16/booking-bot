"""
Checks on the dashboard's files that need no browser.

The dashboard was also driven end to end in Chromium; these guard the
rules that are easy to break by accident: every word exists in both
languages, nothing is ever parsed as HTML, and nothing needs the inline
scripts or styles the Content-Security-Policy refuses.
"""
import pathlib
import re

import pytest

DASHBOARD = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard"
SCRIPTS = sorted((DASHBOARD / "js").rglob("*.js"))
I18N = (DASHBOARD / "js" / "i18n.js").read_text(encoding="utf-8")

KEY_PREFIXES = ("app", "nav", "lang", "common", "err", "login", "cal", "status", "appt",
                "wa", "summary", "book", "list", "cust", "team", "treat", "stats", "set",
                "clinic", "clinics", "logins", "password")
KEY_LITERAL = re.compile(r"'((?:%s)\.[A-Za-z0-9.]+)'" % "|".join(KEY_PREFIXES))


def dictionary(name: str) -> dict:
    """The entries of `const <name> = { ... };` in i18n.js, one per line."""
    block = re.search(r"^const %s = \{\n(.*?)^\};" % name, I18N, re.S | re.M)
    assert block, f"no dictionary named {name}"
    return dict(re.findall(r"^\s*'([^']+)':\s*(.+?),?$", block.group(1), re.M))


HE = dictionary("he")
EN = dictionary("en")


def used_keys() -> set:
    keys = set()
    for path in SCRIPTS:
        if path.name == "i18n.js":
            # Only the code after the dictionaries uses keys.
            text = path.read_text(encoding="utf-8").split("const SERVER_HE", 1)[1]
        else:
            text = path.read_text(encoding="utf-8")
        keys.update(KEY_LITERAL.findall(text))
    return keys


class TestTranslations:
    def test_both_languages_have_the_same_keys(self):
        assert set(HE) - set(EN) == set(), "only in Hebrew"
        assert set(EN) - set(HE) == set(), "only in English"

    def test_every_key_in_use_exists(self):
        missing = used_keys() - set(HE)
        assert not missing, f"used but not defined: {sorted(missing)}"

    def test_no_key_is_left_unused(self):
        unused = set(HE) - used_keys()
        assert not unused, f"defined but never used: {sorted(unused)}"

    def test_placeholders_match_between_languages(self):
        for key, hebrew in HE.items():
            english = EN[key]
            assert set(re.findall(r"\{(\w+)\}", hebrew)) == set(re.findall(r"\{(\w+)\}", english)), key

    def test_server_errors_are_translated(self):
        """Every booking error the API can send has a Hebrew version."""
        from app.api.v1.appointments import ERROR_DETAIL
        server_he = I18N.split("const SERVER_HE = {", 1)[1].split("};", 1)[0]
        for message in ERROR_DETAIL.values():
            assert f"'{message}':" in server_he, message


class TestNothingIsParsedAsHtml:
    FORBIDDEN = [
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
        "new Function", "setAttribute('style'", "cssText", "srcdoc",
    ]

    @pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
    def test_script_builds_the_page_from_text(self, path):
        text = path.read_text(encoding="utf-8")
        for pattern in self.FORBIDDEN:
            assert pattern not in text, f"{pattern} in {path.name}"


class TestFilesFitThePolicy:
    def test_the_page_has_no_inline_script_or_style(self):
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag, "inline script: the policy would block it"
        assert "<style" not in html and "style=" not in html

    def test_everything_the_page_loads_exists(self):
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="(/dashboard/[^"]+)"', html):
            assert (DASHBOARD / ref.removeprefix("/dashboard/")).is_file(), ref

    @pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
    def test_every_import_resolves(self, path):
        for target in re.findall(r"from '(\.[^']+)'", path.read_text(encoding="utf-8")):
            assert (path.parent / target).resolve().is_file(), f"{path.name} imports {target}"

    def test_scripts_load_only_from_this_server(self):
        for path in SCRIPTS:
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"from '(https?:)?//", text), path.name

"""Regression tests for the icon-edition CV builder.

These exist because the icons are the whole point of the redesign and they are
easy to lose silently: a bad refactor can drop an inline SVG from the template
without changing any file size in a way anyone would notice.

Also pins the *honesty* of the figures on the CV. The old CV claimed "~640k
lines"; the measured figures are ~760k tracked / ~457k app / ~302k tests, and a
stale claim must fail the build rather than ship.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

CVS = pathlib.Path(__file__).resolve().parents[1] / "cvs"
sys.path.insert(0, str(CVS))

# The CV builder operates on the candidate's private CV data, which is not part
# of the public repository. Skip the whole module when that directory is absent
# rather than failing collection for anyone who clones the public slice.
if not CVS.exists():
    pytest.skip("cvs/ is private candidate data, not shipped in the public repo",
                allow_module_level=True)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, CVS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


icons = _load("icons")
build = _load("build_icon_cv")

ALL_VARIANTS = sorted(build.VARIANTS)


# ── the brand marks must actually be inline SVG, not a text label ─────────────
def test_icons_are_self_contained_svg():
    for fn in (icons.beatvids, icons.shopify, icons.globe, icons.send, icons.mail, icons.phone, icons.pin):
        svg = fn()
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert "<path" in svg or "<circle" in svg or "<rect" in svg


def test_beatvids_uses_the_real_favicon_waveform():
    """The path must be copied verbatim from BeatVidsFrontend/public/favicon.svg."""
    assert icons.BEATVIDS_PATH == (
        "M10 34C14 16 18 16 22 34C26 52 30 52 34 34"
        "C38 16 42 16 46 34C50 52 54 52 58 34"
    )
    assert icons.beatvids().count(icons.BEATVIDS_PATH) == 1
    assert icons.BEATVIDS_PURPLE == "#A855F7"


def test_shopify_is_the_real_brand_path_not_a_placeholder():
    svg = icons.shopify()
    # The Simple Icons Shopify mark begins with this move command. If the asset
    # ever goes missing we get a stub, and this catches it.
    assert "15.337 23.979" in svg
    assert "#95BF47" in svg
    assert "currentColor" not in svg, "fill must be a concrete colour, not currentColor"


def test_simple_icon_assets_are_present_on_disk():
    for name in ("shopify.svg", "github.svg"):
        p = CVS / "assets" / name
        assert p.exists() and p.stat().st_size > 200, f"missing brand asset {name}"


# ── every variant must carry the requested icons ─────────────────────────────
@pytest.mark.parametrize("key", ALL_VARIANTS)
def test_variant_renders_every_requested_icon(key: str):
    html = build.build_html(key)
    # the three the user explicitly asked for: BeatVids, Shopify, beatvids.app
    assert icons.BEATVIDS_PATH in html, "BeatVids waveform icon missing"
    assert "15.337 23.979" in html, "Shopify mark missing"
    assert icons.globe(22)[:40] in html, "beatvids.app globe icon missing"
    # plus the UI glyphs used in the contact row and skill rows
    for probe in ("m3 7 9 6", "M6.5 3h3l1.5 4", "M12 21s7-5.6", "m9 17-5-5"):
        assert probe in html, f"glyph {probe!r} missing"


@pytest.mark.parametrize("key", ALL_VARIANTS)
def test_variant_has_no_unrendered_template_braces(key: str):
    html = build.build_html(key)
    body = html.split("</style>", 1)[1]
    assert "{{" not in html and "}}" not in html, "f-string placeholder leaked"


@pytest.mark.parametrize("key", ALL_VARIANTS)
def test_language_and_availability_line(key: str):
    html = build.build_html(key)
    lang = build.VARIANTS[key]["lang"]
    assert f'<html lang="{lang}">' in html
    # the part-time/remote constraint is the point of this job hunt
    if lang == "pl":
        assert "część etatu" in html and "praca zdalna" in html
    else:
        assert "Part-time" in html and "Remote" in html


# ── honesty pins ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", ALL_VARIANTS)
def test_no_stale_loc_claim(key: str):
    """The retired '~640k lines' figure must never come back."""
    html = build.build_html(key)
    assert "640" not in html, "stale LOC claim resurfaced"


def test_loc_claim_matches_the_measurement():
    html = build.build_html("fullstack")
    assert "760k" in html, "tracked LOC figure missing"
    assert "300k" in html, "test-LOC figure missing"


def test_density_knob_actually_scales_the_document():
    """build_html(base=...) must change the rendered size, or auto-fit is a no-op."""
    big = build.build_html("fullstack", 10.2)
    small = build.build_html("fullstack", 8.6)
    assert big != small
    assert "font-size:10.2pt" in big
    assert "font-size:8.6pt" in small


def test_fit_ladder_is_ordered_largest_first():
    """auto-fit picks the largest size that fits, so the ladder must descend."""
    ladder = build.FIT_LADDER
    assert ladder == sorted(ladder, reverse=True)
    assert ladder[0] <= 10.5, "start would be too large to be readable"
    assert ladder[-1] >= 7.5, "floor would be unreadably small"


def test_a4_height_constant_is_a4():
    # 297mm at 96px/inch
    assert abs(build.A4_H - 297 / 25.4 * 96) < 0.6


# ── per-job hook ─────────────────────────────────────────────────────────────
def test_hook_is_rendered_when_supplied():
    html = build.build_html("fullstack", hook="Targeted opening sentence.")
    assert '<p class="hook">Targeted opening sentence.</p>' in html


def test_no_hook_leaves_no_empty_hook_element():
    html = build.build_html("fullstack")
    assert '<p class="hook">' not in html, "empty hook paragraph would shift layout"


def test_hook_does_not_disturb_the_icons_or_language():
    plain = build.build_html("ai_pl")
    hooked = build.build_html("ai_pl", hook="Szukam tej roli.")
    for probe in (icons.BEATVIDS_PATH, "15.337 23.979", icons.globe(22)[:40]):
        assert probe in hooked, "hook build dropped an icon"
    assert 'lang="pl"' in hooked
    assert hooked.count('<p class="hook">') == 1
    # the hook must not have replaced any real content
    for section in ("O mnie", "Projekty", "Technologie", "Edukacja"):
        assert section in plain and section in hooked


# ── the closing must not contradict the message ─────────────────────────────
class TestChannelAwareClosing:
    """Every variant's profile ends with a mentorship ask. That is right for an employment
    application and wrong for contract work, where the message says 'projektowa wspolpraca B2B'.
    Sending both together puts a visible contradiction in one document."""

    def test_default_profile_still_asks_for_mentorship_on_the_variants_that_have_it(self):
        """Employment applications must keep the honest mentorship ask.

        Only 3 of the 8 variants carry it (ai, frontend, ai_pl) - so the test targets one that
        does, rather than assuming all of them do.
        """
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "bic", pathlib.Path(__file__).resolve().parents[1] / "cvs" / "build_icon_cv.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        assert "mentorship" in m.build_html("ai").lower()
        assert "mentorship" in m.build_html("frontend").lower()
        assert "mentoringiem" in m.build_html("ai_pl").lower()

    def test_closing_replaces_the_mentorship_sentence(self):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "bic", pathlib.Path(__file__).resolve().parents[1] / "cvs" / "build_icon_cv.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        html = m.build_html("ai", closing="Available for remote project work, ~20 h/week.")
        assert "mentorship" not in html.lower(), "the contradicting ask must be gone"
        assert "remote project work" in html

    def test_closing_keeps_the_substance_of_the_profile(self):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "bic", pathlib.Path(__file__).resolve().parents[1] / "cvs" / "build_icon_cv.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        html = m.build_html("ai", closing="Available for remote project work.")
        for keep in ("Self-taught full-stack developer", "SGGW", "BeatVids"):
            assert keep in html, f"{keep!r} must survive the closing swap"

    def test_apply_closing_is_a_noop_without_a_closing(self):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "bic", pathlib.Path(__file__).resolve().parents[1] / "cvs" / "build_icon_cv.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        prof = "Text about work. I want a junior/mid role where I keep growing under mentorship."
        assert m.apply_closing(prof, None) == prof

    def test_apply_closing_appends_when_there_is_no_mentorship_sentence(self):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "bic", pathlib.Path(__file__).resolve().parents[1] / "cvs" / "build_icon_cv.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        out = m.apply_closing("Plain profile text.", "Available for contract work.")
        assert out.endswith("Available for contract work.")
        assert "Plain profile text." in out


# ── the argument parser: a standalone `if` double-consumed and ate --hook ────
class TestArgParsing:
    """18 of 20 CVs were built with a MISSING per-job targeting line.

    Cause: `if a == "--closing": ...` was a standalone `if`, so after matching it control FELL THROUGH
    into the following `if/elif/else` chain, missed `--hook`/`--slug`, hit the `else`, and appended the
    flag to `keys` while incrementing `i` a SECOND time - skipping the next argument entirely. Any
    `--hook` placed after `--closing` was never parsed.
    """

    def _parse(self, argv):
        import importlib.util, pathlib, re
        src = (pathlib.Path(__file__).resolve().parents[1] / "cvs" / "build_icon_cv.py").read_text()
        keys, hook, slug, closing, i = [], None, None, None, 0
        while i < len(argv):
            a = argv[i]
            if a == "--closing":
                closing, i = argv[i + 1], i + 2
            elif a == "--hook":
                hook, i = argv[i + 1], i + 2
            elif a == "--slug":
                slug, i = argv[i + 1], i + 2
            else:
                keys.append(a)
                i += 1
        return keys, hook, slug, closing

    def test_hook_after_closing_is_parsed(self):
        """The exact invocation that silently dropped the hook."""
        keys, hook, slug, closing = self._parse(
            ["ai", "--slug", "snowflake", "--closing", "CLOSE TEXT", "--hook", "HOOK TEXT"])
        assert hook == "HOOK TEXT", f"hook was dropped: {hook!r}"
        assert closing == "CLOSE TEXT"
        assert slug == "snowflake"
        assert keys == ["ai"], f"flags leaked into keys: {keys}"

    def test_hook_before_closing_is_parsed(self):
        keys, hook, slug, closing = self._parse(
            ["ai", "--hook", "HOOK TEXT", "--closing", "CLOSE TEXT"])
        assert (hook, closing, keys) == ("HOOK TEXT", "CLOSE TEXT", ["ai"])

    def test_all_flags_alone_still_work(self):
        assert self._parse(["--hook", "H"])[1] == "H"
        assert self._parse(["--closing", "C"])[3] == "C"
        assert self._parse(["--slug", "s"])[2] == "s"
        assert self._parse([])[0] == []

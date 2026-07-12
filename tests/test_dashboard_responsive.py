from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_hero_uses_compact_accessible_navigation():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    Environment().parse(template)
    assert 'class="hero-menu-toggle"' in template
    assert 'aria-controls="hero-nav"' in template
    assert ".hero-nav.is-open" in template
    assert "menuToggle.setAttribute('aria-expanded'" in template
    assert "event.key === 'Escape'" in template


def test_mobile_hero_limits_density_and_viewport_shift():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    assert "100dvh" in template
    assert "100vh" not in template
    assert "min-height:520px" in template
    assert ".hero-title span:nth-child(2),.hero-title span:nth-child(3) { display:inline" in template
    assert 'fetchpriority="high"' in template
    assert "—" not in template
    assert "–" not in template

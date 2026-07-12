from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_uses_compact_accessible_navigation_shell():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    Environment().parse(template)
    assert 'class="shell-menu-toggle"' in template
    assert 'aria-controls="home-sidebar"' in template
    assert ".home-sidebar.is-open" in template
    assert 'class="home-topbar"' in template
    assert 'class="home-sidebar"' in template
    assert "经营中心" in template
    assert "数据与模型" in template
    assert "menuToggle.setAttribute('aria-expanded'" in template
    assert "event.key === 'Escape'" in template


def test_mobile_hero_limits_density_and_viewport_shift():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    assert "100dvh" in template
    assert "100vh" not in template
    assert "min-height:520px" in template
    assert ".hero-title span:nth-child(2),.hero-title span:nth-child(3) { display:inline" in template
    assert ".hero-title span:nth-child(2) { transform" not in template
    assert ".hero-title span:nth-child(3) { transform" not in template
    assert "font-size:108px" not in template
    assert 'fetchpriority="high"' in template
    assert "—" not in template
    assert "–" not in template


def test_dashboard_integrates_line_waves_with_safe_fallbacks():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    script = (ROOT / "app" / "static" / "js" / "line-waves.js").read_text(encoding="utf-8")

    assert "data-line-waves" in template
    assert "js/line-waves.js" in template
    assert "hero-media" in template
    assert "ogl@1.0.11/dist/ogl.mjs" in script
    assert "prefers-reduced-motion" in script
    assert "Math.min(window.devicePixelRatio || 1, 1.5)" in script
    assert "ResizeObserver" in script
    assert "IntersectionObserver" in script
    assert "visibilitychange" in script

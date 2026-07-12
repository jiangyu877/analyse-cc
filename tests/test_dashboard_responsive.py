import re
import subprocess
from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]


def _has_class(source, class_name):
    class_attributes = re.finditer(
        r"(?<![\w:-])class\s*=\s*(['\"])(.*?)\1",
        source,
        re.DOTALL,
    )
    return any(class_name in match.group(2).split() for match in class_attributes)


def _selector_has_declaration(source, selector, property_name, value):
    rule = re.search(
        rf"(?m)^\s*{re.escape(selector)}\s*\{{(?P<declarations>[^}}]*)\}}",
        source,
    )
    return bool(
        rule
        and re.search(
            rf"(?<![\w-]){re.escape(property_name)}\s*:\s*{re.escape(value)}\s*(?:;|$)",
            rule.group("declarations"),
        )
    )


def _magic_bento_nav(template):
    marker_pattern = re.compile(
        r"(?<![\w:-])data-magic-bento(?=\s|=|$)"
    )
    attribute_pattern = re.compile(
        r"(?<![\w:-])(?P<name>[\w:-]+)(?=\s|=|$)"
        r"(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s]+))?",
        re.DOTALL,
    )
    nav_pattern = re.compile(r"<nav\b(?P<attributes>[^>]*)>.*?</nav>", re.DOTALL)

    for match in nav_pattern.finditer(template):
        attributes = attribute_pattern.finditer(match.group("attributes"))
        if any(marker_pattern.fullmatch(attr.group("name")) for attr in attributes):
            return match.group(0)

    raise AssertionError("data-magic-bento must be an attribute on a nav element")


def test_dashboard_uses_accessible_magic_bento_navigation():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    Environment().parse(template)
    assert _has_class(template, "home-topbar")
    assert _has_class(template, "bento-section")
    assert _has_class(template, "bento-grid")
    assert not _has_class(template, "home-sidebar")
    assert not _has_class(template, "shell-menu-toggle")
    assert "event.key === 'Escape'" not in template

    bento_nav = _magic_bento_nav(template)
    assert 'aria-label="业务功能"' in bento_nav

    for endpoint in (
        "customers.index",
        "products.index",
        "orders.index",
        "payments.index",
        "refunds.index",
        "algorithms.index",
    ):
        assert f"url_for('{endpoint}')" in bento_nav


def test_dashboard_keeps_admin_bento_links_permission_gated():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    bento_nav = _magic_bento_nav(template)
    admin_guard = "{% if session.get('role') == 'admin' %}"
    admin_end = "{% endif %}"

    assert admin_guard in bento_nav
    nav_before_guard, guarded_tail = bento_nav.split(admin_guard, 1)
    assert admin_end in guarded_tail
    guarded_content, nav_after_guard = guarded_tail.split(admin_end, 1)
    nav_without_guarded_block = nav_before_guard + nav_after_guard

    for endpoint in ("imports.index", "custom_query.custom_query_page"):
        route_reference = f"url_for('{endpoint}')"
        assert bento_nav.count(route_reference) == 1
        assert route_reference in guarded_content
        assert route_reference not in nav_without_guarded_block


def test_mobile_hero_limits_density_and_viewport_shift():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    assert re.search(
        r"<h2\b[^>]*(?<![\w:-])id\s*=\s*(['\"])bento-title\1[^>]*>",
        template,
        re.DOTALL,
    )
    for selector in (".bento-section", "#bento-title"):
        assert _selector_has_declaration(
            template,
            selector,
            "scroll-margin-top",
            "56px",
        )
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
    assert "getContext('webgl'" in script
    assert "compileShader" in script
    assert "gl.drawArrays(gl.TRIANGLES, 0, 3)" in script
    assert "cdn.jsdelivr.net/npm/ogl" not in script
    assert "prefers-reduced-motion" in script
    assert "Math.min(window.devicePixelRatio || 1, 1.5)" in script
    assert "ResizeObserver" in script
    assert "IntersectionObserver" in script
    assert "visibilitychange" in script


def test_dashboard_magic_bento_enhancement_is_optional_and_motion_safe():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    script_path = ROOT / "app" / "static" / "js" / "magic-bento.js"

    assert "js/magic-bento.js" in template
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    for behavior in (
        "requestAnimationFrame",
        "prefers-reduced-motion",
        "(pointer: fine)",
        "--glow-x",
        "--glow-y",
        "--glow-intensity",
        "bento-particle",
        "pagehide",
        "pageshow",
        "pointercancel",
        "event.persisted",
        "cancelAnimationFrame",
        "removeEventListener",
    ):
        assert behavior in script


def test_dashboard_magic_bento_lifecycle_behavior():
    behavior_test = ROOT / "tests" / "magic_bento_lifecycle.test.cjs"

    try:
        result = subprocess.run(
            ["node", "--test", str(behavior_test)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as error:
        raise AssertionError("Node.js is required for the Magic Bento lifecycle test") from error

    assert result.returncode == 0, (
        "Magic Bento lifecycle test failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_dashboard_magic_bento_matches_reference_grid_and_glow_language():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    for reference_style in (
        "--bento-purple-rgb:132,0,255",
        "--glow-radius:200px",
        "padding:6px",
        "grid-row:span 2",
        "width:800px",
        "height:800px",
        "border-radius:50%",
        "mix-blend-mode:screen",
    ):
        assert reference_style in template

    assert ".bento-card--wide { grid-column:span 2; grid-row:span 2; min-height:376px" in template
    assert ".bento-card--admin { grid-column:span 2" in template
    assert "background:rgba(var(--bento-purple-rgb),.9)" in template

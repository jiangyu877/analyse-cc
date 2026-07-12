# Homepage Hero Black Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render every homepage Hero text element in black on a lightened version of the existing artwork without changing the topbar or Bento palette.

**Architecture:** Keep the change inside the existing inline dashboard CSS. Add Hero-scoped ink and divider tokens, map all Hero text and divider selectors to those tokens, and replace the dark Hero shade with the approved light neutral overlay. Preserve all HTML, JavaScript, routes, and responsive layout.

**Tech Stack:** Flask, Jinja, native CSS, pytest, Playwright browser verification, Git.

---

### Task 1: Add the Hero palette contract and minimal CSS implementation

**Files:**
- Modify: `tests/test_dashboard_responsive.py`
- Modify: `app/templates/dashboard.html`

- [ ] **Step 1: Write the failing Hero palette test**

Add this test to `tests/test_dashboard_responsive.py`:

```python
def test_dashboard_hero_uses_black_text_on_a_light_surface():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    assert "--hero-ink:#000" in template
    assert "--hero-line:rgba(0,0,0,.24)" in template
    for selector, property_name, value in (
        (".hero", "color", "var(--hero-ink)"),
        (".hero-shade", "background", "rgba(247,248,246,.72)"),
        (".hero-note", "color", "var(--hero-ink)"),
        (".hero-metrics", "border-top", "1px solid var(--hero-line)"),
        (".hero-metric + .hero-metric", "border-left", "1px solid var(--hero-line)"),
        (".hero-metric small", "color", "var(--hero-ink)"),
        (".hero-metric:nth-child(3) strong", "color", "var(--hero-ink)"),
        (".hero-metric:nth-child(4) strong", "color", "var(--hero-ink)"),
    ):
        assert _selector_has_declaration(template, selector, property_name, value)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dashboard_responsive.py::test_dashboard_hero_uses_black_text_on_a_light_surface -q
```

Expected: FAIL because `--hero-ink:#000` is absent.

- [ ] **Step 3: Add Hero-scoped palette tokens**

Add to the existing `:root` rule in `app/templates/dashboard.html`:

```css
--hero-ink:#000;
--hero-line:rgba(0,0,0,.24);
```

- [ ] **Step 4: Apply the approved Hero colors**

Make these exact CSS changes in `app/templates/dashboard.html`:

```css
.hero {
  color:var(--hero-ink);
}

.hero-shade {
  position:absolute;
  inset:0;
  background:rgba(247,248,246,.72);
  z-index:-2;
}

.hero-note { color:var(--hero-ink); }
.hero-metrics { border-top:1px solid var(--hero-line); }
.hero-metric + .hero-metric { border-left:1px solid var(--hero-line); }
.hero-metric small { color:var(--hero-ink); }
.hero-metric:nth-child(3) strong { color:var(--hero-ink); }
.hero-metric:nth-child(4) strong { color:var(--hero-ink); }
```

Within the existing mobile media rule, change the third and fourth metric `border-top` colors from `var(--home-line)` to `var(--hero-line)`.

- [ ] **Step 5: Run focused and responsive tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dashboard_responsive.py::test_dashboard_hero_uses_black_text_on_a_light_surface -q
.\.venv\Scripts\python.exe -m pytest tests\test_dashboard_responsive.py -q
```

Expected: focused test passes; all dashboard responsive tests pass.

### Task 2: Verify rendering and regressions

**Files:**
- Verify: `app/templates/dashboard.html`
- Verify: `tests/test_dashboard_responsive.py`

- [ ] **Step 1: Start the application without the Flask reloader**

Run `serve.py` with `HOST=127.0.0.1`, `PORT=5001`, and `FLASK_DEBUG=false`.

- [ ] **Step 2: Render authenticated desktop and mobile views**

Use Playwright at `1440 x 1000` and `390 x 844`. Store screenshots outside the repository under the Codex visualization directory.

- [ ] **Step 3: Verify computed styles and layout**

Check all of the following in the browser:

```text
Hero kicker, title, note, metric labels, and metric values: rgb(0, 0, 0)
Topbar text: unchanged light color
Bento title and cards: unchanged light color
Horizontal overflow: 0
Hero text overlap pairs: 0
```

- [ ] **Step 4: Run the complete automated suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app\static\js\line-waves.js
node --check app\static\js\magic-bento.js
```

Expected: all pytest tests pass and both JavaScript checks exit `0`.

### Task 3: Commit and push the approved change

**Files:**
- Modify: `.gitignore`
- Add: `docs/superpowers/specs/2026-07-13-hero-black-text-design.md`
- Add: `docs/superpowers/plans/2026-07-13-hero-black-text.md`
- Modify: `app/templates/dashboard.html`
- Modify: `tests/test_dashboard_responsive.py`

- [ ] **Step 1: Inspect the final diff and sensitive files**

Run `git status --short`, `git diff --check`, and confirm `.firecrawl/` content is ignored and not staged.

- [ ] **Step 2: Stage only the intended files**

```powershell
git add .gitignore app/templates/dashboard.html tests/test_dashboard_responsive.py docs/superpowers/specs/2026-07-13-hero-black-text-design.md docs/superpowers/plans/2026-07-13-hero-black-text.md
```

- [ ] **Step 3: Commit**

```powershell
git commit -m "Use black text in dashboard hero"
```

- [ ] **Step 4: Rebase on and push `main`**

```powershell
git pull --rebase origin main
git push origin main
```

- [ ] **Step 5: Verify the remote tracking update**

Confirm `HEAD` equals `origin/main`, the working tree is clean, and the `origin/main` reflog records `update by push` for the new commit.

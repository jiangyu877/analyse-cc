# Magic Bento Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard-only sidebar with a responsive Magic Bento module navigation while preserving routes, permissions, accessibility, and graceful no-JavaScript behavior.

**Architecture:** `dashboard.html` owns semantic navigation markup and responsive presentation. A focused `magic-bento.js` progressively enhances the links with pointer glow and short-lived particles, while every card remains a normal anchor. `test_dashboard_responsive.py` protects the template, permissions, breakpoints, and enhancement contract.

**Tech Stack:** Flask, Jinja2, native CSS Grid, native browser JavaScript, Tabler Icons, pytest, Node syntax checker.

---

### Task 1: Replace the sidebar contract with the Bento contract

**Files:**
- Modify: `tests/test_dashboard_responsive.py`
- Test: `tests/test_dashboard_responsive.py`

- [ ] **Step 1: Write the failing template contract test**

Replace `test_dashboard_uses_compact_accessible_navigation_shell` with:

```python
def test_dashboard_uses_accessible_magic_bento_navigation():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    Environment().parse(template)
    assert 'class="home-topbar"' in template
    assert 'class="bento-section"' in template
    assert 'class="bento-grid"' in template
    assert 'data-magic-bento' in template
    assert 'aria-label="业务功能"' in template
    assert 'class="home-sidebar"' not in template
    assert 'class="shell-menu-toggle"' not in template
    assert "event.key === 'Escape'" not in template

    for endpoint in (
        "customers.index",
        "products.index",
        "orders.index",
        "payments.index",
        "refunds.index",
        "algorithms.index",
    ):
        assert f"url_for('{endpoint}')" in template


def test_dashboard_keeps_admin_bento_links_permission_gated():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    guard = "{% if session.get('role') == 'admin' %}"
    assert guard in template
    admin_block = template.split(guard, 1)[1].split("{% endif %}", 1)[0]
    assert "url_for('imports.index')" in admin_block
    assert "url_for('custom_query.custom_query_page')" in admin_block
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_dashboard_responsive.py::test_dashboard_uses_accessible_magic_bento_navigation tests/test_dashboard_responsive.py::test_dashboard_keeps_admin_bento_links_permission_gated -q
```

Expected: the first test fails because `.bento-section` is absent and the old sidebar is still present.

- [ ] **Step 3: Commit the failing contract when Git metadata is writable**

```powershell
C:\Users\jiang\cmd\git.exe add tests/test_dashboard_responsive.py
C:\Users\jiang\cmd\git.exe commit -m "test: define Magic Bento dashboard contract"
```

### Task 2: Implement the semantic Bento layout and responsive styling

**Files:**
- Modify: `app/templates/dashboard.html`
- Test: `tests/test_dashboard_responsive.py`

- [ ] **Step 1: Remove dashboard-only sidebar code**

Remove these selectors and their declarations from the inline stylesheet:

```css
.shell-menu-toggle
.home-layout
.home-sidebar
.sidebar-group
.sidebar-label
.home-side-link
.sidebar-backdrop
body.sidebar-open
```

Remove the `.shell-menu-toggle` button, `<aside class="home-sidebar">`, `.sidebar-backdrop`, the extra `.home-layout`/`.home-main` wrappers, and the sidebar JavaScript block. Keep `.home-topbar`, `.hero`, `.home-content`, Chart.js initialization, and logout form unchanged.

- [ ] **Step 2: Add Bento design tokens and layout CSS**

Add the following tokens to `:root`:

```css
--bento-bg:#120f17;
--bento-border:#2f293a;
--bento-purple:132,0,255;
--bento-purple-solid:#8400ff;
```

Add the Bento styles before the existing operations styles:

```css
.bento-section { max-width:1480px; margin:0 auto; padding:42px 42px 8px; position:relative; isolation:isolate; }
.bento-heading { display:flex; align-items:end; justify-content:space-between; gap:24px; margin-bottom:22px; }
.bento-heading h2 { margin:0; font-size:32px; line-height:1.05; letter-spacing:0; }
.bento-heading p { max-width:440px; margin:0; color:var(--home-muted); font-size:13px; line-height:1.6; }
.bento-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); grid-auto-flow:dense; gap:8px; position:relative; }
.bento-card { --glow-x:50%; --glow-y:50%; --glow-intensity:0; position:relative; min-width:0; min-height:190px; padding:20px; overflow:hidden; display:flex; flex-direction:column; justify-content:space-between; gap:26px; border:1px solid var(--bento-border); border-radius:8px; background:var(--bento-bg); color:var(--home-text); text-decoration:none; transition:transform .2s ease,border-color .2s ease,box-shadow .2s ease; }
.bento-card::after { content:""; position:absolute; inset:0; padding:1px; border-radius:inherit; background:radial-gradient(240px circle at var(--glow-x) var(--glow-y),rgba(var(--bento-purple),calc(var(--glow-intensity) * .9)),transparent 62%); -webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0); -webkit-mask-composite:xor; mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0); mask-composite:exclude; pointer-events:none; }
.bento-card:hover,.bento-card:focus-visible { transform:translateY(-2px); border-color:rgba(var(--bento-purple),.68); box-shadow:0 12px 30px rgba(0,0,0,.28),0 0 24px rgba(var(--bento-purple),.18); }
.bento-card:focus-visible { outline:2px solid var(--home-green); outline-offset:3px; }
.bento-card--feature { grid-column:span 2; grid-row:span 2; min-height:388px; }
.bento-card--wide { grid-column:span 2; }
.bento-card__top { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; color:var(--home-muted); }
.bento-card__icon { width:38px; height:38px; display:grid; place-items:center; border:1px solid var(--home-line); border-radius:7px; color:var(--home-green); }
.bento-card__icon i { font-size:20px; }
.bento-card__arrow { font-size:19px; }
.bento-card__label { margin:0 0 8px; color:var(--home-muted); font-size:11px; }
.bento-card__title { margin:0 0 7px; font-size:21px; line-height:1.15; letter-spacing:0; }
.bento-card--feature .bento-card__title { font-size:29px; }
.bento-card__description { margin:0; color:#c8c5cd; font-size:12px; line-height:1.55; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; }
.bento-spotlight { position:absolute; width:520px; height:520px; left:0; top:0; border-radius:50%; opacity:0; transform:translate(-50%,-50%); background:radial-gradient(circle,rgba(var(--bento-purple),.12),transparent 68%); pointer-events:none; transition:opacity .18s ease; z-index:2; }
.bento-particle { position:absolute; width:4px; height:4px; border-radius:50%; background:rgba(var(--bento-purple),.9); box-shadow:0 0 7px rgba(var(--bento-purple),.62); pointer-events:none; animation:bento-particle-out .75s ease-out forwards; }
@keyframes bento-particle-out { to { opacity:0; transform:translate(var(--particle-x),var(--particle-y)) scale(.2); } }

@media(max-width:1023px) {
  .bento-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .bento-card--feature,.bento-card--wide { grid-column:span 2; grid-row:auto; min-height:220px; }
}
@media(max-width:599px) {
  .bento-section { padding:34px 18px 8px; }
  .bento-heading { align-items:flex-start; flex-direction:column; gap:10px; }
  .bento-heading h2 { font-size:28px; }
  .bento-grid { grid-template-columns:1fr; }
  .bento-card,.bento-card--feature,.bento-card--wide { grid-column:auto; min-height:168px; padding:18px; }
}
@media(prefers-reduced-motion:reduce) {
  .bento-card { transition:none; }
  .bento-card:hover,.bento-card:focus-visible { transform:none; }
  .bento-particle,.bento-spotlight { display:none; }
}
```

- [ ] **Step 3: Add semantic navigation after the Hero**

Insert the complete section immediately after the Hero closing tag:

```html
<section class="bento-section" aria-labelledby="bento-title">
  <div class="bento-heading">
    <h2 id="bento-title">业务功能</h2>
    <p>客户、商品、交易与模型</p>
  </div>
  <nav class="bento-grid" data-magic-bento aria-label="业务功能">
    <a class="bento-card" href="{{ url_for('customers.index') }}">
      <span class="bento-card__top">
        <span class="bento-card__icon"><i class="ti ti-users" aria-hidden="true"></i></span>
        <i class="ti ti-arrow-up-right bento-card__arrow" aria-hidden="true"></i>
      </span>
      <span class="bento-card__content">
        <span class="bento-card__label">客户洞察</span>
        <strong class="bento-card__title">客户 360</strong>
        <span class="bento-card__description">识别高价值客户、活跃度与消费特征。</span>
      </span>
    </a>
    <a class="bento-card" href="{{ url_for('products.index') }}">
      <span class="bento-card__top">
        <span class="bento-card__icon"><i class="ti ti-package" aria-hidden="true"></i></span>
        <i class="ti ti-arrow-up-right bento-card__arrow" aria-hidden="true"></i>
      </span>
      <span class="bento-card__content">
        <span class="bento-card__label">商品经营</span>
        <strong class="bento-card__title">商品库存</strong>
        <span class="bento-card__description">掌握商品表现、库存水平与缺货风险。</span>
      </span>
    </a>
    <a class="bento-card bento-card--feature" href="{{ url_for('algorithms.index') }}">
      <span class="bento-card__top">
        <span class="bento-card__icon"><i class="ti ti-chart-dots-3" aria-hidden="true"></i></span>
        <i class="ti ti-arrow-up-right bento-card__arrow" aria-hidden="true"></i>
      </span>
      <span class="bento-card__content">
        <span class="bento-card__label">智能分析</span>
        <strong class="bento-card__title">算法任务</strong>
        <span class="bento-card__description">运行客户聚类、销量预测与商品关联分析。</span>
      </span>
    </a>
    <a class="bento-card bento-card--wide" href="{{ url_for('orders.index') }}">
      <span class="bento-card__top">
        <span class="bento-card__icon"><i class="ti ti-receipt" aria-hidden="true"></i></span>
        <i class="ti ti-arrow-up-right bento-card__arrow" aria-hidden="true"></i>
      </span>
      <span class="bento-card__content">
        <span class="bento-card__label">交易管理</span>
        <strong class="bento-card__title">订单业务</strong>
        <span class="bento-card__description">查看订单状态、交易明细与履约过程。</span>
      </span>
    </a>
    <a class="bento-card" href="{{ url_for('payments.index') }}">
      <span class="bento-card__top">
        <span class="bento-card__icon"><i class="ti ti-credit-card" aria-hidden="true"></i></span>
        <i class="ti ti-arrow-up-right bento-card__arrow" aria-hidden="true"></i>
      </span>
      <span class="bento-card__content">
        <span class="bento-card__label">资金记录</span>
        <strong class="bento-card__title">支付记录</strong>
        <span class="bento-card__description">核对支付流水、金额与交易时间。</span>
      </span>
    </a>
    <a class="bento-card" href="{{ url_for('refunds.index') }}">
      <span class="bento-card__top">
        <span class="bento-card__icon"><i class="ti ti-cash-banknote-off" aria-hidden="true"></i></span>
        <i class="ti ti-arrow-up-right bento-card__arrow" aria-hidden="true"></i>
      </span>
      <span class="bento-card__content">
        <span class="bento-card__label">售后管理</span>
        <strong class="bento-card__title">退款管理</strong>
        <span class="bento-card__description">追踪退款原因、处理状态与净交易影响。</span>
      </span>
    </a>
    {% if session.get('role') == 'admin' %}
    <a class="bento-card" href="{{ url_for('imports.index') }}">
      <span class="bento-card__top">
        <span class="bento-card__icon"><i class="ti ti-database-import" aria-hidden="true"></i></span>
        <i class="ti ti-arrow-up-right bento-card__arrow" aria-hidden="true"></i>
      </span>
      <span class="bento-card__content">
        <span class="bento-card__label">数据管理</span>
        <strong class="bento-card__title">数据导入</strong>
        <span class="bento-card__description">导入业务数据并更新经营分析口径。</span>
      </span>
    </a>
    <a class="bento-card" href="{{ url_for('custom_query.custom_query_page') }}">
      <span class="bento-card__top">
        <span class="bento-card__icon"><i class="ti ti-database-search" aria-hidden="true"></i></span>
        <i class="ti ti-arrow-up-right bento-card__arrow" aria-hidden="true"></i>
      </span>
      <span class="bento-card__content">
        <span class="bento-card__label">数据查询</span>
        <strong class="bento-card__title">只读 SQL</strong>
        <span class="bento-card__description">执行受控查询并核验数据库事实。</span>
      </span>
    </a>
    {% endif %}
    <span class="bento-spotlight" aria-hidden="true"></span>
  </nav>
</section>
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_dashboard_responsive.py::test_dashboard_uses_accessible_magic_bento_navigation tests/test_dashboard_responsive.py::test_dashboard_keeps_admin_bento_links_permission_gated -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit the semantic layout when Git metadata is writable**

```powershell
C:\Users\jiang\cmd\git.exe add app/templates/dashboard.html tests/test_dashboard_responsive.py
C:\Users\jiang\cmd\git.exe commit -m "feat: add dashboard Magic Bento navigation"
```

### Task 3: Add progressive pointer enhancement

**Files:**
- Create: `app/static/js/magic-bento.js`
- Modify: `app/templates/dashboard.html`
- Modify: `tests/test_dashboard_responsive.py`
- Test: `tests/test_dashboard_responsive.py`

- [ ] **Step 1: Write the failing enhancement contract test**

Append:

```python
def test_dashboard_magic_bento_enhancement_is_optional_and_motion_safe():
    template = (ROOT / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    script_path = ROOT / "app" / "static" / "js" / "magic-bento.js"

    assert "js/magic-bento.js" in template
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")
    assert "requestAnimationFrame" in script
    assert "prefers-reduced-motion" in script
    assert "(pointer: fine)" in script
    assert "--glow-x" in script
    assert "--glow-intensity" in script
    assert "bento-particle" in script
    assert "pagehide" in script
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_dashboard_responsive.py::test_dashboard_magic_bento_enhancement_is_optional_and_motion_safe -q
```

Expected: FAIL because `js/magic-bento.js` is not referenced.

- [ ] **Step 3: Implement `magic-bento.js`**

Create a dependency-free module with this lifecycle:

```javascript
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const finePointer = window.matchMedia('(pointer: fine)');

document.querySelectorAll('[data-magic-bento]').forEach((grid) => {
  const cards = Array.from(grid.querySelectorAll('.bento-card'));
  const spotlight = grid.querySelector('.bento-spotlight');
  let frame = 0;
  let pointerX = 0;
  let pointerY = 0;
  let lastParticleAt = 0;

  const reset = () => {
    cards.forEach((card) => card.style.setProperty('--glow-intensity', '0'));
    if (spotlight) spotlight.style.opacity = '0';
  };

  const render = () => {
    frame = 0;
    const gridRect = grid.getBoundingClientRect();
    if (spotlight) {
      spotlight.style.left = `${pointerX - gridRect.left}px`;
      spotlight.style.top = `${pointerY - gridRect.top}px`;
      spotlight.style.opacity = '1';
    }

    cards.forEach((card) => {
      const rect = card.getBoundingClientRect();
      const x = pointerX - rect.left;
      const y = pointerY - rect.top;
      const inside = x >= 0 && y >= 0 && x <= rect.width && y <= rect.height;
      card.style.setProperty('--glow-x', `${x}px`);
      card.style.setProperty('--glow-y', `${y}px`);
      card.style.setProperty('--glow-intensity', inside ? '1' : '.22');
    });
  };

  const addParticle = (event) => {
    const card = event.target.closest('.bento-card');
    if (!card || performance.now() - lastParticleAt < 90) return;
    lastParticleAt = performance.now();
    const rect = card.getBoundingClientRect();
    const particle = document.createElement('span');
    particle.className = 'bento-particle';
    particle.setAttribute('aria-hidden', 'true');
    particle.style.left = `${event.clientX - rect.left}px`;
    particle.style.top = `${event.clientY - rect.top}px`;
    particle.style.setProperty('--particle-x', `${(Math.random() - .5) * 44}px`);
    particle.style.setProperty('--particle-y', `${(Math.random() - .5) * 44}px`);
    card.appendChild(particle);
    particle.addEventListener('animationend', () => particle.remove(), { once: true });
  };

  const onPointerMove = (event) => {
    pointerX = event.clientX;
    pointerY = event.clientY;
    if (!frame) frame = requestAnimationFrame(render);
    addParticle(event);
  };

  if (!reduceMotion.matches && finePointer.matches) {
    grid.addEventListener('pointermove', onPointerMove);
    grid.addEventListener('pointerleave', reset);
  }

  window.addEventListener('pagehide', () => {
    if (frame) cancelAnimationFrame(frame);
    grid.removeEventListener('pointermove', onPointerMove);
    grid.removeEventListener('pointerleave', reset);
    grid.querySelectorAll('.bento-particle').forEach((particle) => particle.remove());
  }, { once: true });
});
```

Add this after the LineWaves script tag:

```html
<script type="module" src="{{ url_for('static', filename='js/magic-bento.js') }}"></script>
```

- [ ] **Step 4: Verify the enhancement**

Run:

```powershell
node --check app/static/js/magic-bento.js
python -m pytest tests/test_dashboard_responsive.py -q
```

Expected: Node exits `0`; all dashboard tests pass.

- [ ] **Step 5: Commit the enhancement when Git metadata is writable**

```powershell
C:\Users\jiang\cmd\git.exe add app/static/js/magic-bento.js app/templates/dashboard.html tests/test_dashboard_responsive.py
C:\Users\jiang\cmd\git.exe commit -m "feat: enhance Bento navigation interactions"
```

### Task 4: Verify rendering, accessibility, and deployment handoff

**Files:**
- Modify: `push_to_github.cmd`
- Verify: `app/templates/dashboard.html`
- Verify: `app/static/js/magic-bento.js`
- Verify: `tests/test_dashboard_responsive.py`

- [ ] **Step 1: Update the push helper commit message**

Change the existing fallback message to:

```cmd
set "COMMIT_MESSAGE=Add Magic Bento dashboard navigation"
```

- [ ] **Step 2: Run full automated verification**

Run:

```powershell
python -m pytest -q
node --check app/static/js/line-waves.js
node --check app/static/js/magic-bento.js
C:\Users\jiang\cmd\git.exe diff --check
```

Expected: the full pytest suite passes, both Node checks exit `0`, and `git diff --check` reports no errors.

- [ ] **Step 3: Render desktop and mobile screenshots**

Start the Flask application with its normal development command, then capture:

```text
Desktop: 1440 x 1000
Mobile: 390 x 844
```

Verify the Hero remains visible, the Bento section follows it, six standard-user cards have no empty fixed slots, admin cards fill automatically, no text overlaps, and the page has no horizontal scroll.

- [ ] **Step 4: Verify keyboard and reduced-motion behavior**

Tab through every Bento link and confirm a visible focus ring. Emulate `prefers-reduced-motion: reduce` and confirm particles and spotlight are absent while every link remains clickable.

- [ ] **Step 5: Prepare deployment**

When Git write/network approval is available, double-click `push_to_github.cmd`. Expected final message: `Push completed successfully.` Render then deploys the new `main` commit automatically.

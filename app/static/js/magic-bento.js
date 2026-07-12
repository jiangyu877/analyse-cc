const PARTICLE_LIMIT = 8;
const PARTICLE_INTERVAL = 96;
const PARTICLE_LIFETIME = 700;
const NEARBY_RADIUS = 220;
const NEARBY_INTENSITY = 0.16;

function addMediaChangeListener(query, listener) {
  if (typeof query.addEventListener === 'function') {
    query.addEventListener('change', listener);
  } else if (typeof query.addListener === 'function') {
    query.addListener(listener);
  }
}

function removeMediaChangeListener(query, listener) {
  if (typeof query.removeEventListener === 'function') {
    query.removeEventListener('change', listener);
  } else if (typeof query.removeListener === 'function') {
    query.removeListener(listener);
  }
}

function pointIsInside(x, y, rect) {
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

function distanceFromRect(x, y, rect) {
  const distanceX = Math.max(rect.left - x, 0, x - rect.right);
  const distanceY = Math.max(rect.top - y, 0, y - rect.bottom);
  return Math.hypot(distanceX, distanceY);
}

function mountMagicBento(grid) {
  const cards = Array.from(grid.querySelectorAll('.bento-card'));
  const spotlight = grid.querySelector('.bento-spotlight');
  const particles = new Map();
  let enabled = false;
  let destroyed = false;
  let frameId = null;
  let pointerX = 0;
  let pointerY = 0;
  let hoveredCard = null;
  let lastParticleTime = Number.NEGATIVE_INFINITY;

  function removeParticle(particle) {
    const state = particles.get(particle);
    if (!state) return;

    window.clearTimeout(state.timeoutId);
    particle.removeEventListener('animationend', state.handleAnimationEnd);
    particle.remove();
    particles.delete(particle);
  }

  function clearParticles() {
    Array.from(particles.keys()).forEach(removeParticle);
  }

  function spawnParticle(card, rect, timestamp) {
    if (timestamp - lastParticleTime < PARTICLE_INTERVAL) return;
    lastParticleTime = timestamp;

    if (particles.size >= PARTICLE_LIMIT) {
      removeParticle(particles.keys().next().value);
    }

    const particle = document.createElement('span');
    const offsetX = Math.round((Math.random() - 0.5) * 24);
    const offsetY = -Math.round(14 + Math.random() * 18);
    particle.className = 'bento-particle';
    particle.setAttribute('aria-hidden', 'true');
    particle.style.left = `${pointerX - rect.left}px`;
    particle.style.top = `${pointerY - rect.top}px`;
    particle.style.setProperty('--particle-x', `${offsetX}px`);
    particle.style.setProperty('--particle-y', `${offsetY}px`);

    const handleAnimationEnd = () => removeParticle(particle);
    particle.addEventListener('animationend', handleAnimationEnd, { once: true });
    const timeoutId = window.setTimeout(() => removeParticle(particle), PARTICLE_LIFETIME);
    particles.set(particle, { handleAnimationEnd, timeoutId });
    card.appendChild(particle);
  }

  function resetInteraction() {
    hoveredCard = null;
    cards.forEach((card) => {
      card.style.removeProperty('--glow-x');
      card.style.removeProperty('--glow-y');
      card.style.setProperty('--glow-intensity', '0');
    });
    if (spotlight) {
      spotlight.classList.remove('is-visible');
      spotlight.style.removeProperty('transform');
    }
    clearParticles();
  }

  function cancelPendingFrame() {
    if (frameId === null) return;
    cancelAnimationFrame(frameId);
    frameId = null;
  }

  function renderPointer(timestamp) {
    frameId = null;
    if (!enabled || destroyed) return;

    const gridRect = spotlight ? grid.getBoundingClientRect() : null;
    const measurements = cards.map((card) => ({
      card,
      rect: card.getBoundingClientRect()
    }));
    const current = measurements.find(({ rect }) => pointIsInside(pointerX, pointerY, rect)) || null;
    const nextHoveredCard = current ? current.card : null;

    if (hoveredCard !== nextHoveredCard) clearParticles();
    hoveredCard = nextHoveredCard;

    measurements.forEach(({ card, rect }) => {
      const distance = distanceFromRect(pointerX, pointerY, rect);
      const proximity = Math.max(0, 1 - distance / NEARBY_RADIUS);
      const intensity = card === hoveredCard ? 1 : NEARBY_INTENSITY * proximity;
      card.style.setProperty('--glow-x', `${pointerX - rect.left}px`);
      card.style.setProperty('--glow-y', `${pointerY - rect.top}px`);
      card.style.setProperty('--glow-intensity', intensity.toFixed(3));
    });

    if (spotlight && gridRect) {
      const x = pointerX - gridRect.left;
      const y = pointerY - gridRect.top;
      spotlight.style.transform = `translate3d(${x}px, ${y}px, 0) translate3d(-50%, -50%, 0)`;
      spotlight.classList.add('is-visible');
    }

    if (current) spawnParticle(current.card, current.rect, timestamp);
  }

  function handlePointerMove(event) {
    if (event.pointerType === 'touch') {
      handlePointerCancel();
      return;
    }
    pointerX = event.clientX;
    pointerY = event.clientY;
    if (frameId === null) frameId = requestAnimationFrame(renderPointer);
  }

  function handlePointerCancel() {
    cancelPendingFrame();
    resetInteraction();
  }

  function handlePointerLeave() {
    handlePointerCancel();
  }

  function enable() {
    if (enabled || destroyed || cards.length === 0) return;
    enabled = true;
    grid.addEventListener('pointermove', handlePointerMove, { passive: true });
    grid.addEventListener('pointerleave', handlePointerLeave);
    grid.addEventListener('pointercancel', handlePointerCancel);
  }

  function disable() {
    if (enabled) {
      grid.removeEventListener('pointermove', handlePointerMove);
      grid.removeEventListener('pointerleave', handlePointerLeave);
      grid.removeEventListener('pointercancel', handlePointerCancel);
    }
    enabled = false;
    cancelPendingFrame();
    resetInteraction();
  }

  function sync(shouldEnable) {
    if (shouldEnable) enable(); else disable();
  }

  function destroy() {
    if (destroyed) return;
    disable();
    destroyed = true;
    cards.forEach((card) => {
      card.style.removeProperty('--glow-x');
      card.style.removeProperty('--glow-y');
      card.style.removeProperty('--glow-intensity');
    });
  }

  return { destroy, sync };
}

function initMagicBento() {
  const grids = Array.from(document.querySelectorAll('[data-magic-bento]'));
  if (grids.length === 0 || typeof window.matchMedia !== 'function') return;

  let reducedMotionQuery;
  let finePointerQuery;
  try {
    reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    finePointerQuery = window.matchMedia('(pointer: fine)');
  } catch (error) {
    return;
  }

  const controllers = grids.map(mountMagicBento);
  let cleanedUp = false;

  function enhancementIsEnabled() {
    return !reducedMotionQuery.matches && finePointerQuery.matches;
  }

  function handleMediaChange() {
    const shouldEnable = enhancementIsEnabled();
    controllers.forEach((controller) => controller.sync(shouldEnable));
  }

  function handlePageHide(event) {
    if (event.persisted) {
      controllers.forEach((controller) => controller.sync(false));
      return;
    }
    if (cleanedUp) return;
    cleanedUp = true;
    controllers.forEach((controller) => controller.destroy());
    removeMediaChangeListener(reducedMotionQuery, handleMediaChange);
    removeMediaChangeListener(finePointerQuery, handleMediaChange);
    window.removeEventListener('pagehide', handlePageHide);
    window.removeEventListener('pageshow', handlePageShow);
  }

  function handlePageShow(event) {
    if (!event.persisted || cleanedUp) return;
    handleMediaChange();
  }

  addMediaChangeListener(reducedMotionQuery, handleMediaChange);
  addMediaChangeListener(finePointerQuery, handleMediaChange);
  window.addEventListener('pagehide', handlePageHide);
  window.addEventListener('pageshow', handlePageShow);
  handleMediaChange();
}

initMagicBento();

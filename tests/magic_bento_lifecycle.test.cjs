const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener, options = {}) {
    const listeners = this.listeners.get(type) || [];
    if (!listeners.some((entry) => entry.listener === listener)) {
      listeners.push({ listener, once: Boolean(options && options.once) });
      this.listeners.set(type, listeners);
    }
  }

  removeEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    const remaining = listeners.filter((entry) => entry.listener !== listener);
    if (remaining.length > 0) this.listeners.set(type, remaining);
    else this.listeners.delete(type);
  }

  dispatchEvent(event) {
    event.target = this;
    for (const entry of [...(this.listeners.get(event.type) || [])]) {
      entry.listener.call(this, event);
      if (entry.once) this.removeEventListener(event.type, entry.listener);
    }
    return true;
  }

  listenerCount(type) {
    return (this.listeners.get(type) || []).length;
  }
}

class FakeStyle {
  constructor() {
    this.values = new Map();
  }

  setProperty(name, value) {
    this.values.set(name, String(value));
  }

  getPropertyValue(name) {
    if (this.values.has(name)) return this.values.get(name);
    return typeof this[name] === 'string' ? this[name] : '';
  }

  removeProperty(name) {
    const previous = this.getPropertyValue(name);
    this.values.delete(name);
    delete this[name];
    return previous;
  }
}

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  add(name) {
    this.values.add(name);
  }

  remove(name) {
    this.values.delete(name);
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeElement extends FakeEventTarget {
  constructor(rect = { left: 0, top: 0, right: 0, bottom: 0 }) {
    super();
    this.rect = rect;
    this.style = new FakeStyle();
    this.classList = new FakeClassList();
    this.className = '';
    this.attributes = new Map();
    this.children = [];
    this.parentNode = null;
  }

  getBoundingClientRect() {
    return this.rect;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
}

class FakeMediaQuery extends FakeEventTarget {
  constructor(matches) {
    super();
    this.matches = matches;
  }

  emitChange() {
    this.dispatchEvent({ type: 'change', matches: this.matches });
  }
}

function createHarness() {
  const card = new FakeElement({ left: 0, top: 0, right: 100, bottom: 100 });
  const spotlight = new FakeElement();
  const grid = new FakeElement({ left: 0, top: 0, right: 300, bottom: 200 });
  grid.querySelectorAll = (selector) => selector === '.bento-card' ? [card] : [];
  grid.querySelector = (selector) => selector === '.bento-spotlight' ? spotlight : null;

  const reducedMotionQuery = new FakeMediaQuery(false);
  const finePointerQuery = new FakeMediaQuery(true);
  const sandbox = new FakeEventTarget();
  sandbox.window = sandbox;
  sandbox.matchMedia = (query) => {
    if (query === '(prefers-reduced-motion: reduce)') return reducedMotionQuery;
    if (query === '(pointer: fine)') return finePointerQuery;
    throw new Error(`Unexpected media query: ${query}`);
  };

  let nextTimerId = 1;
  const timers = new Map();
  sandbox.setTimeout = (callback) => {
    const id = nextTimerId++;
    timers.set(id, callback);
    return id;
  };
  sandbox.clearTimeout = (id) => timers.delete(id);

  let nextFrameId = 1;
  const frames = new Map();
  sandbox.requestAnimationFrame = (callback) => {
    const id = nextFrameId++;
    frames.set(id, callback);
    return id;
  };
  sandbox.cancelAnimationFrame = (id) => frames.delete(id);
  const flushAnimationFrames = (timestamp) => {
    const callbacks = [...frames.values()];
    frames.clear();
    callbacks.forEach((callback) => callback(timestamp));
  };

  const document = {
    querySelectorAll: (selector) => selector === '[data-magic-bento]' ? [grid] : [],
    createElement: () => new FakeElement()
  };
  sandbox.document = document;
  const sourcePath = path.resolve(__dirname, '..', 'app', 'static', 'js', 'magic-bento.js');
  const source = fs.readFileSync(sourcePath, 'utf8');
  const context = vm.createContext(sandbox);
  assert.equal(vm.runInContext('window === globalThis', context), true);
  assert.equal(vm.runInContext(
    'requestAnimationFrame === window.requestAnimationFrame'
      + ' && cancelAnimationFrame === window.cancelAnimationFrame'
      + ' && setTimeout === window.setTimeout'
      + ' && clearTimeout === window.clearTimeout',
    context
  ), true);
  vm.runInContext(source, context, { filename: sourcePath });

  return {
    card,
    finePointerQuery,
    flushAnimationFrames,
    frames,
    grid,
    reducedMotionQuery,
    spotlight,
    timers,
    window: sandbox
  };
}

function dispatchPointer(grid, pointerType = 'mouse') {
  grid.dispatchEvent({
    type: 'pointermove',
    pointerType,
    clientX: 20,
    clientY: 25
  });
}

function assertGridListeners(grid, expected) {
  for (const type of ['pointermove', 'pointerleave', 'pointercancel']) {
    assert.equal(grid.listenerCount(type), expected, `${type} listener count`);
  }
}

function assertInteractionReset(harness, intensity = '0') {
  assert.equal(harness.frames.size, 0, 'pending animation frames');
  assert.equal(harness.timers.size, 0, 'pending particle timers');
  assert.equal(harness.card.children.length, 0, 'rendered particles');
  assert.equal(harness.card.style.getPropertyValue('--glow-x'), '');
  assert.equal(harness.card.style.getPropertyValue('--glow-y'), '');
  assert.equal(harness.card.style.getPropertyValue('--glow-intensity'), intensity);
  assert.equal(harness.spotlight.classList.contains('is-visible'), false);
  assert.equal(harness.spotlight.style.getPropertyValue('transform'), '');
}

test('Magic Bento filters touch input and survives repeated BFCache lifecycle cycles', () => {
  const harness = createHarness();
  const {
    card,
    finePointerQuery,
    flushAnimationFrames,
    frames,
    grid,
    reducedMotionQuery,
    spotlight,
    timers,
    window
  } = harness;

  assertGridListeners(grid, 1);
  assert.equal(reducedMotionQuery.listenerCount('change'), 1);
  assert.equal(finePointerQuery.listenerCount('change'), 1);
  assert.equal(window.listenerCount('pagehide'), 1);
  assert.equal(window.listenerCount('pageshow'), 1);

  dispatchPointer(grid);
  assert.equal(frames.size, 1, 'mouse movement schedules one frame');
  flushAnimationFrames(100);
  assert.equal(frames.size, 0);
  assert.equal(card.style.getPropertyValue('--glow-intensity'), '1.000');
  assert.equal(spotlight.classList.contains('is-visible'), true);
  assert.match(spotlight.style.getPropertyValue('transform'), /^translate3d\(20px, 25px, 0\)/);
  assert.equal(card.children.length, 1, 'mouse movement renders a particle');
  assert.equal(card.children[0].className, 'bento-particle');
  assert.equal(timers.size, 1);

  dispatchPointer(grid);
  assert.equal(frames.size, 1, 'a mouse frame is pending before touch input');
  dispatchPointer(grid, 'touch');
  assertInteractionReset(harness);
  flushAnimationFrames(200);
  assertInteractionReset(harness);

  dispatchPointer(grid);
  flushAnimationFrames(300);
  dispatchPointer(grid);
  assert.equal(frames.size, 1, 'a mouse frame is pending before pointer cancellation');
  grid.dispatchEvent({ type: 'pointercancel', pointerType: 'mouse' });
  assertInteractionReset(harness);

  finePointerQuery.emitChange();
  finePointerQuery.emitChange();
  assertGridListeners(grid, 1);
  reducedMotionQuery.matches = true;
  reducedMotionQuery.emitChange();
  reducedMotionQuery.emitChange();
  assertGridListeners(grid, 0);
  reducedMotionQuery.matches = false;
  reducedMotionQuery.emitChange();
  reducedMotionQuery.emitChange();
  assertGridListeners(grid, 1);

  for (let cycle = 0; cycle < 2; cycle += 1) {
    dispatchPointer(grid);
    flushAnimationFrames(500 + cycle * 200);
    dispatchPointer(grid);
    assert.equal(frames.size, 1, `cycle ${cycle + 1} starts with a pending frame`);
    assert.equal(card.children.length, 1, `cycle ${cycle + 1} starts with a particle`);

    window.dispatchEvent({ type: 'pagehide', persisted: true });
    assertGridListeners(grid, 0);
    assertInteractionReset(harness);
    assert.equal(reducedMotionQuery.listenerCount('change'), 1);
    assert.equal(finePointerQuery.listenerCount('change'), 1);
    assert.equal(window.listenerCount('pagehide'), 1);
    assert.equal(window.listenerCount('pageshow'), 1);

    window.dispatchEvent({ type: 'pageshow', persisted: true });
    assertGridListeners(grid, 1);
    assert.equal(window.listenerCount('pagehide'), 1);
    assert.equal(window.listenerCount('pageshow'), 1);
  }

  dispatchPointer(grid);
  flushAnimationFrames(1000);
  dispatchPointer(grid);
  assert.equal(frames.size, 1, 'permanent cleanup starts with a pending frame');
  assert.equal(card.children.length, 1, 'permanent cleanup starts with a particle');

  window.dispatchEvent({ type: 'pagehide', persisted: false });
  assertGridListeners(grid, 0);
  assert.equal(reducedMotionQuery.listenerCount('change'), 0);
  assert.equal(finePointerQuery.listenerCount('change'), 0);
  assert.equal(window.listenerCount('pagehide'), 0);
  assert.equal(window.listenerCount('pageshow'), 0);
  assertInteractionReset(harness, '');

  window.dispatchEvent({ type: 'pageshow', persisted: true });
  finePointerQuery.emitChange();
  reducedMotionQuery.emitChange();
  dispatchPointer(grid);
  assertGridListeners(grid, 0);
  assertInteractionReset(harness, '');
});

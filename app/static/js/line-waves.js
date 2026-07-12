import { Mesh, Program, Renderer, Triangle } from 'https://cdn.jsdelivr.net/npm/ogl@1.0.11/dist/ogl.mjs';

function hexToVec3(hex) {
  const value = hex.replace('#', '');
  return [
    parseInt(value.slice(0, 2), 16) / 255,
    parseInt(value.slice(2, 4), 16) / 255,
    parseInt(value.slice(4, 6), 16) / 255
  ];
}

const vertexShader = `
attribute vec2 uv;
attribute vec2 position;
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position, 0, 1);
}
`;

const fragmentShader = `
precision highp float;

uniform float uTime;
uniform vec3 uResolution;
uniform float uSpeed;
uniform float uInnerLines;
uniform float uOuterLines;
uniform float uWarpIntensity;
uniform float uRotation;
uniform float uEdgeFadeWidth;
uniform float uColorCycleSpeed;
uniform float uBrightness;
uniform vec3 uColor1;
uniform vec3 uColor2;
uniform vec3 uColor3;
uniform vec2 uMouse;
uniform float uMouseInfluence;
uniform bool uEnableMouse;

#define HALF_PI 1.5707963

float hashF(float n) {
  return fract(sin(n * 127.1) * 43758.5453123);
}

float smoothNoise(float x) {
  float i = floor(x);
  float f = fract(x);
  float u = f * f * (3.0 - 2.0 * f);
  return mix(hashF(i), hashF(i + 1.0), u);
}

float displaceA(float coord, float t) {
  float result = sin(coord * 2.123) * 0.2;
  result += sin(coord * 3.234 + t * 4.345) * 0.1;
  result += sin(coord * 0.589 + t * 0.934) * 0.5;
  return result;
}

float displaceB(float coord, float t) {
  float result = sin(coord * 1.345) * 0.3;
  result += sin(coord * 2.734 + t * 3.345) * 0.2;
  result += sin(coord * 0.189 + t * 0.934) * 0.3;
  return result;
}

vec2 rotate2D(vec2 p, float angle) {
  float c = cos(angle);
  float s = sin(angle);
  return vec2(p.x * c - p.y * s, p.x * s + p.y * c);
}

void main() {
  vec2 coords = gl_FragCoord.xy / uResolution.xy;
  coords = coords * 2.0 - 1.0;
  coords = rotate2D(coords, uRotation);

  float halfT = uTime * uSpeed * 0.5;
  float fullT = uTime * uSpeed;

  float mouseWarp = 0.0;
  if (uEnableMouse) {
    vec2 mPos = rotate2D(uMouse * 2.0 - 1.0, uRotation);
    float mDist = length(coords - mPos);
    mouseWarp = uMouseInfluence * exp(-mDist * mDist * 4.0);
  }

  float warpAx = coords.x + displaceA(coords.y, halfT) * uWarpIntensity + mouseWarp;
  float warpAy = coords.y - displaceA(coords.x * cos(fullT) * 1.235, halfT) * uWarpIntensity;
  float warpBx = coords.x + displaceB(coords.y, halfT) * uWarpIntensity + mouseWarp;
  float warpBy = coords.y - displaceB(coords.x * sin(fullT) * 1.235, halfT) * uWarpIntensity;

  vec2 fieldA = vec2(warpAx, warpAy);
  vec2 fieldB = vec2(warpBx, warpBy);
  vec2 blended = mix(fieldA, fieldB, mix(fieldA, fieldB, 0.5));

  float fadeTop = smoothstep(uEdgeFadeWidth, uEdgeFadeWidth + 0.4, blended.y);
  float fadeBottom = smoothstep(-uEdgeFadeWidth, -(uEdgeFadeWidth + 0.4), blended.y);
  float vMask = 1.0 - max(fadeTop, fadeBottom);

  float tileCount = mix(uOuterLines, uInnerLines, vMask);
  float scaledY = blended.y * tileCount;
  float nY = smoothNoise(abs(scaledY));

  float ridge = pow(
    step(abs(nY - blended.x) * 2.0, HALF_PI) * cos(2.0 * (nY - blended.x)),
    5.0
  );

  float lines = 0.0;
  for (float i = 1.0; i < 3.0; i += 1.0) {
    lines += pow(max(fract(scaledY), fract(-scaledY)), i * 2.0);
  }

  float pattern = vMask * lines;
  float cycleT = fullT * uColorCycleSpeed;
  float rChannel = (pattern + lines * ridge) * (cos(blended.y + cycleT * 0.234) * 0.5 + 1.0);
  float gChannel = (pattern + vMask * ridge) * (sin(blended.x + cycleT * 1.745) * 0.5 + 1.0);
  float bChannel = (pattern + lines * ridge) * (cos(blended.x + cycleT * 0.534) * 0.5 + 1.0);

  vec3 col = (rChannel * uColor1 + gChannel * uColor2 + bChannel * uColor3) * uBrightness;
  float alpha = clamp(length(col), 0.0, 1.0);
  gl_FragColor = vec4(col, alpha);
}
`;

function numberOption(element, name, fallback) {
  const value = Number(element.dataset[name]);
  return Number.isFinite(value) ? value : fallback;
}

function mountLineWaves(container) {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = window.matchMedia('(pointer: fine)').matches;
  const enableMouse = finePointer && !reduceMotion;
  const renderer = new Renderer({
    alpha: true,
    premultipliedAlpha: false,
    dpr: Math.min(window.devicePixelRatio || 1, 1.5)
  });
  const gl = renderer.gl;
  gl.clearColor(0, 0, 0, 0);

  const geometry = new Triangle(gl);
  const rotation = numberOption(container, 'rotation', -45) * Math.PI / 180;
  const program = new Program(gl, {
    vertex: vertexShader,
    fragment: fragmentShader,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    uniforms: {
      uTime: { value: 0 },
      uResolution: { value: [1, 1, 1] },
      uSpeed: { value: numberOption(container, 'speed', 0.3) },
      uInnerLines: { value: numberOption(container, 'innerLines', 32) },
      uOuterLines: { value: numberOption(container, 'outerLines', 36) },
      uWarpIntensity: { value: numberOption(container, 'warp', 1) },
      uRotation: { value: rotation },
      uEdgeFadeWidth: { value: numberOption(container, 'edgeFade', 0) },
      uColorCycleSpeed: { value: numberOption(container, 'colorCycle', 1) },
      uBrightness: { value: numberOption(container, 'brightness', 0.2) },
      uColor1: { value: hexToVec3(container.dataset.color1 || '#ffffff') },
      uColor2: { value: hexToVec3(container.dataset.color2 || '#ffffff') },
      uColor3: { value: hexToVec3(container.dataset.color3 || '#ffffff') },
      uMouse: { value: new Float32Array([0.5, 0.5]) },
      uMouseInfluence: { value: numberOption(container, 'mouseInfluence', 2) },
      uEnableMouse: { value: enableMouse }
    }
  });
  const mesh = new Mesh(gl, { geometry, program });
  container.appendChild(gl.canvas);

  let currentMouse = [0.5, 0.5];
  let targetMouse = [0.5, 0.5];
  let animationFrameId = 0;
  let visible = true;

  function resize() {
    const width = Math.max(1, container.clientWidth);
    const height = Math.max(1, container.clientHeight);
    renderer.setSize(width, height);
    program.uniforms.uResolution.value = [gl.canvas.width, gl.canvas.height, gl.canvas.width / gl.canvas.height];
    if (reduceMotion) renderer.render({ scene: mesh });
  }

  function handlePointerMove(event) {
    const rect = container.getBoundingClientRect();
    targetMouse = [
      (event.clientX - rect.left) / rect.width,
      1 - (event.clientY - rect.top) / rect.height
    ];
  }

  function resetPointer() {
    targetMouse = [0.5, 0.5];
  }

  function update(time) {
    animationFrameId = 0;
    if (!visible || document.hidden) return;
    program.uniforms.uTime.value = time * 0.001;
    if (enableMouse) {
      currentMouse[0] += 0.05 * (targetMouse[0] - currentMouse[0]);
      currentMouse[1] += 0.05 * (targetMouse[1] - currentMouse[1]);
      program.uniforms.uMouse.value[0] = currentMouse[0];
      program.uniforms.uMouse.value[1] = currentMouse[1];
    }
    renderer.render({ scene: mesh });
    animationFrameId = requestAnimationFrame(update);
  }

  function start() {
    if (reduceMotion) {
      renderer.render({ scene: mesh });
    } else if (!animationFrameId && visible && !document.hidden) {
      animationFrameId = requestAnimationFrame(update);
    }
  }

  function stop() {
    if (animationFrameId) cancelAnimationFrame(animationFrameId);
    animationFrameId = 0;
  }

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container);
  const intersectionObserver = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    if (visible) start(); else stop();
  }, { threshold: 0.01 });
  intersectionObserver.observe(container);

  const pointerSurface = container.parentElement;
  if (enableMouse && pointerSurface) {
    pointerSurface.addEventListener('pointermove', handlePointerMove, { passive: true });
    pointerSurface.addEventListener('pointerleave', resetPointer);
  }
  const handleVisibility = () => document.hidden ? stop() : start();
  document.addEventListener('visibilitychange', handleVisibility);

  resize();
  start();

  return function cleanup() {
    stop();
    resizeObserver.disconnect();
    intersectionObserver.disconnect();
    document.removeEventListener('visibilitychange', handleVisibility);
    if (enableMouse && pointerSurface) {
      pointerSurface.removeEventListener('pointermove', handlePointerMove);
      pointerSurface.removeEventListener('pointerleave', resetPointer);
    }
    gl.canvas.remove();
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  };
}

const cleanups = [];
document.querySelectorAll('[data-line-waves]').forEach((container) => {
  try {
    cleanups.push(mountLineWaves(container));
  } catch (error) {
    console.warn('LineWaves background unavailable; using image fallback.', error);
  }
});

window.addEventListener('pagehide', () => cleanups.forEach((cleanup) => cleanup()), { once: true });

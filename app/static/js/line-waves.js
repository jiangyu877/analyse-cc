function hexToVec3(hex) {
  const value = hex.replace('#', '');
  return [
    parseInt(value.slice(0, 2), 16) / 255,
    parseInt(value.slice(2, 4), 16) / 255,
    parseInt(value.slice(4, 6), 16) / 255
  ];
}

const vertexShader = `
attribute vec2 position;
varying vec2 vUv;
void main() {
  vUv = position * 0.5 + 0.5;
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

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(shader) || 'Unknown shader compilation error';
    gl.deleteShader(shader);
    throw new Error(message);
  }
  return shader;
}

function createWaveSurface(container, settings) {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl', {
    alpha: true,
    antialias: false,
    premultipliedAlpha: false,
    powerPreference: 'low-power'
  });
  if (!gl) throw new Error('WebGL is unavailable');

  const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexShader);
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fragmentShader);
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const message = gl.getProgramInfoLog(program) || 'Unknown shader link error';
    gl.deleteProgram(program);
    throw new Error(message);
  }

  const positionBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const positionLocation = gl.getAttribLocation(program, 'position');
  gl.enableVertexAttribArray(positionLocation);
  gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0);

  const uniformNames = [
    'uTime', 'uResolution', 'uSpeed', 'uInnerLines', 'uOuterLines',
    'uWarpIntensity', 'uRotation', 'uEdgeFadeWidth', 'uColorCycleSpeed',
    'uBrightness', 'uColor1', 'uColor2', 'uColor3', 'uMouse',
    'uMouseInfluence', 'uEnableMouse'
  ];
  const uniforms = Object.fromEntries(uniformNames.map((name) => [name, gl.getUniformLocation(program, name)]));
  let resolution = [1, 1, 1];

  gl.useProgram(program);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);
  gl.uniform1f(uniforms.uSpeed, settings.speed);
  gl.uniform1f(uniforms.uInnerLines, settings.innerLines);
  gl.uniform1f(uniforms.uOuterLines, settings.outerLines);
  gl.uniform1f(uniforms.uWarpIntensity, settings.warp);
  gl.uniform1f(uniforms.uRotation, settings.rotation);
  gl.uniform1f(uniforms.uEdgeFadeWidth, settings.edgeFade);
  gl.uniform1f(uniforms.uColorCycleSpeed, settings.colorCycle);
  gl.uniform1f(uniforms.uBrightness, settings.brightness);
  gl.uniform3fv(uniforms.uColor1, settings.color1);
  gl.uniform3fv(uniforms.uColor2, settings.color2);
  gl.uniform3fv(uniforms.uColor3, settings.color3);
  gl.uniform1f(uniforms.uMouseInfluence, settings.mouseInfluence);
  gl.uniform1i(uniforms.uEnableMouse, settings.enableMouse ? 1 : 0);
  container.appendChild(canvas);

  return {
    canvas,
    resize(width, height) {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
      gl.viewport(0, 0, canvas.width, canvas.height);
      resolution = [canvas.width, canvas.height, canvas.width / canvas.height];
    },
    render(time, mouse) {
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(program);
      gl.uniform1f(uniforms.uTime, time);
      gl.uniform3fv(uniforms.uResolution, resolution);
      gl.uniform2fv(uniforms.uMouse, mouse);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    destroy() {
      gl.deleteBuffer(positionBuffer);
      gl.deleteProgram(program);
      canvas.remove();
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    }
  };
}

function mountLineWaves(container) {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = window.matchMedia('(pointer: fine)').matches;
  const enableMouse = finePointer && !reduceMotion;
  const surface = createWaveSurface(container, {
    speed: numberOption(container, 'speed', 0.3),
    innerLines: numberOption(container, 'innerLines', 32),
    outerLines: numberOption(container, 'outerLines', 36),
    warp: numberOption(container, 'warp', 1),
    rotation: numberOption(container, 'rotation', -45) * Math.PI / 180,
    edgeFade: numberOption(container, 'edgeFade', 0),
    colorCycle: numberOption(container, 'colorCycle', 1),
    brightness: numberOption(container, 'brightness', 0.2),
    color1: hexToVec3(container.dataset.color1 || '#ffffff'),
    color2: hexToVec3(container.dataset.color2 || '#ffffff'),
    color3: hexToVec3(container.dataset.color3 || '#ffffff'),
    mouseInfluence: numberOption(container, 'mouseInfluence', 2),
    enableMouse
  });

  const currentMouse = new Float32Array([0.5, 0.5]);
  let targetMouse = [0.5, 0.5];
  let animationFrameId = 0;
  let visible = true;

  function resize() {
    const width = Math.max(1, container.clientWidth);
    const height = Math.max(1, container.clientHeight);
    surface.resize(width, height);
    if (reduceMotion) surface.render(0, currentMouse);
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
    if (enableMouse) {
      currentMouse[0] += 0.05 * (targetMouse[0] - currentMouse[0]);
      currentMouse[1] += 0.05 * (targetMouse[1] - currentMouse[1]);
    }
    surface.render(time * 0.001, currentMouse);
    animationFrameId = requestAnimationFrame(update);
  }

  function start() {
    if (reduceMotion) {
      surface.render(0, currentMouse);
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
    surface.destroy();
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

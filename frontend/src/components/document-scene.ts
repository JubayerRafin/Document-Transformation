import * as THREE from "three";
export type DocumentSceneController = { setPaused(value: boolean): void; dispose(): void };

export function createDocumentScene(host: HTMLElement, onLost: () => void): DocumentSceneController | null {
  const canvas = document.createElement("canvas");
  let renderer: THREE.WebGLRenderer;
  try {
    const context = canvas.getContext("webgl2", { alpha: true, antialias: true, powerPreference: "low-power" });
    if (!context) return null;
    renderer = new THREE.WebGLRenderer({ canvas, context, alpha: true, antialias: true });
  } catch { return null; }
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const geometry = <T extends THREE.BufferGeometry>(g: T) => { geometries.add(g); return g; };
  const material = (color: number) => {
    const m = new THREE.MeshPhongMaterial({ color, shininess: 45 });
    materials.add(m); return m;
  };
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, .1, 50);
  camera.position.set(0, .15, 7);
  scene.add(new THREE.HemisphereLight(0xeaf7ff, 0x304553, 2.4));
  const key = new THREE.DirectionalLight(0xffffff, 3);
  key.position.set(-3, 5, 5); scene.add(key);
  const assembly = new THREE.Group(); scene.add(assembly);
  const paperGeometry = geometry(new THREE.BoxGeometry(1.65, 2.25, .055));
  const lineGeometry = geometry(new THREE.BoxGeometry(1, .045, .012));
  const ink = material(0x637d8e), black = material(0x1b2c38), lime = material(0x9beeff);
  const pages: THREE.Group[] = [];
  for (let i = 0; i < 3; i++) {
    const page = new THREE.Group();
    page.position.set((i - 1) * .72, i === 1 ? .28 : -.08, i * .22);
    page.rotation.set(-.08, (i - 1) * -.16, [.28, -.08, -.3][i]);
    page.add(new THREE.Mesh(paperGeometry, material([0x536d7f, 0xa6d1ee, 0xeef4f8][i])));
    for (let row = 0; row < 5; row++) {
      const line = new THREE.Mesh(lineGeometry, i === 2 && row > 2 ? black : ink);
      line.position.set(-.12, .76 - row * .19, .038);
      line.scale.x = [.8, 1.1, .92, 1.02, .66][row];
      if (i === 2 && row > 2) line.scale.y = 2.7;
      page.add(line);
    }
    const chart = new THREE.Group();
    for (let column = 0; column < 4; column++) {
      const bar = new THREE.Mesh(geometry(new THREE.BoxGeometry(.18, .2 + column * .1, .015)), i === 1 ? lime : ink);
      bar.position.set(-.45 + column * .29, -.66 + column * .05, .04);
      chart.add(bar);
    }
    page.add(chart); assembly.add(page); pages.push(page);
  }
  const emblem = new THREE.Mesh(geometry(new THREE.IcosahedronGeometry(.27, 0)), lime);
  emblem.position.set(1.9, .9, .6); assembly.add(emblem);
  assembly.rotation.set(-.16, -.25, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setClearColor(0x0c1419, 0);
  canvas.setAttribute("aria-hidden", "true");
  host.append(canvas);
  let disposed = false, lost = false, paused = false, visible = true, frame = 0, last = 0, elapsed = 0;
  let pointerX = 0, pointerY = 0;
  const draw = () => { if (!disposed && !lost) renderer.render(scene, camera); };
  const tick = (now: number) => {
    frame = 0;
    if (disposed || lost || paused || !visible || document.hidden) return;
    if (now - last >= 1000 / 30) {
      elapsed += Math.min((now - (last || now)) / 1000, .06); last = now;
      assembly.rotation.y += (-.25 + pointerX * .14 - assembly.rotation.y) * .08;
      assembly.rotation.x += (-.16 + pointerY * .1 - assembly.rotation.x) * .08;
      pages.forEach((page, i) => { page.position.y = (i === 1 ? .28 : -.08) + Math.sin(elapsed * .85 + i) * .055; });
      emblem.rotation.y = elapsed * .35; draw();
    }
    frame = requestAnimationFrame(tick);
  };
  const sync = () => {
    cancelAnimationFrame(frame); frame = 0; last = 0;
    if (!disposed && !lost && !paused && visible && !document.hidden) frame = requestAnimationFrame(tick);
    else draw();
  };
  const resize = () => {
    if (disposed || lost) return;
    const width = host.clientWidth, height = host.clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix(); draw();
  };
  const move = (event: PointerEvent) => {
    if (event.pointerType === "touch") return;
    const rect = host.getBoundingClientRect();
    pointerX = (event.clientX - rect.left) / rect.width - .5;
    pointerY = (event.clientY - rect.top) / rect.height - .5;
  };
  const leave = () => { pointerX = pointerY = 0; };
  const contextLost = (event: Event) => { event.preventDefault(); lost = true; cancelAnimationFrame(frame); onLost(); };
  const resizeObserver = new ResizeObserver(resize);
  const intersection = new IntersectionObserver(entries => { visible = entries[0]?.isIntersecting ?? false; sync(); });
  resizeObserver.observe(host); intersection.observe(host);
  document.addEventListener("visibilitychange", sync);
  host.addEventListener("pointermove", move); host.addEventListener("pointerleave", leave);
  canvas.addEventListener("webglcontextlost", contextLost);
  resize(); sync();
  return {
    setPaused(value) { paused = value; sync(); },
    dispose() {
      if (disposed) return; disposed = true;
      cancelAnimationFrame(frame); resizeObserver.disconnect(); intersection.disconnect();
      document.removeEventListener("visibilitychange", sync);
      host.removeEventListener("pointermove", move); host.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("webglcontextlost", contextLost);
      geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose());
      renderer.dispose(); renderer.forceContextLoss(); canvas.remove();
    },
  };
}

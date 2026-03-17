import os
import sys
import json
import difflib
import tempfile
import subprocess
import urllib.request
import webbrowser
from collections import defaultdict

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Optional: Litematica support
try:
    from litemapy import Schematic
    HAS_LITEMAPY = True
except Exception:
    HAS_LITEMAPY = False

# Optional: drag-and-drop support for .litematic files
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:
    HAS_DND = False

# Optional: sound (Windows only)
try:
    import winsound
    HAS_WINSOUND = True
except Exception:
    HAS_WINSOUND = False


# -------------------------
# VERSION & UPDATE CONFIG
# -------------------------

CURRENT_VERSION = "1.6.0"
GITHUB_USER = "Lontiii"
GITHUB_REPO = "MerchantsGuildStorageLocator"
GITHUB_BRANCH = "main"

VERSION_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_USER}/"
    f"{GITHUB_REPO}/{GITHUB_BRANCH}/version.txt"
)

INSTALLER_FILENAME = "mysetup.exe"
DOWNLOAD_URL = (
    f"https://github.com/{GITHUB_USER}/"
    f"{GITHUB_REPO}/releases/latest/download/{INSTALLER_FILENAME}"
)

REPO_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}"

# GitHub schematic library folder:
SCHEMATICS_API_URL = (
    f"https://api.github.com/repos/{GITHUB_USER}/"
    f"{GITHUB_REPO}/contents/schematics"
)

TOWER_ORDER = ["North", "East", "South", "West"]


# -------------------------
# VIEWER HTML (Online CDN)
# -------------------------

VIEWER_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Merchants Guild — 3D Viewer</title>
  <link rel="icon" href="data:,">
  <style>
    html, body { margin:0; padding:0; overflow:hidden; background:#111; color:#eee; font-family: Arial; }
    #hud {
      position: fixed; left: 10px; top: 10px; z-index: 10;
      background: rgba(0,0,0,0.55); padding: 10px 12px; border-radius: 10px;
      max-width: 460px;
    }
    #hud button { margin-right: 6px; margin-top: 6px; }
    #info { margin-top: 8px; line-height: 1.35; }
    canvas { display:block; }
  </style>
</head>
<body>
  <div id="hud">
    <div><b>Merchants Guild — Litematica Viewer</b></div>
    <div id="info">Loading...</div>
    <div>
      <button id="btnThis" disabled>Disable THIS block</button>
      <button id="btnType" disabled>Disable ALL of this type</button>
    </div>
  </div>

  <script type="module">
  import * as THREE from './three.module.js';
  import { OrbitControls } from './OrbitControls.js';

  const info = document.getElementById('info');
  const btnThis = document.getElementById('btnThis');
  const btnType = document.getElementById('btnType');

  let scene, camera, renderer, controls, raycaster;
  let meshes = []; // each is {mesh, paletteIndex, blockId, props, positions}
  let lastHit = null;

  async function fetchJsonTry(paths) {
    let lastErr = null;
    for (const p of paths) {
      try {
        const u = new URL(p, window.location.href).toString();
        const r = await fetch(u);
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return await r.json();
      } catch (e) {
        lastErr = e;
      }
    }
    throw lastErr || new Error('fetchJsonTry failed');
  }

  async function existsTry(paths) {
    for (const p of paths) {
      try {
        const u = new URL(p, window.location.href).toString();
        const r = await fetch(u, { method: 'HEAD' });
        if (r.ok) return p;
      } catch {}
    }
    return null;
  }

  function assetPaths(rel) {
    // Pack first, then vanilla
    return [
      `assets_pack/${rel}`,
      `assets_vanilla/${rel}`,
    ];
  }

  function parseNsPath(s) {
    // "minecraft:block/oak_planks" or "block/oak_planks"
    let ns = 'minecraft';
    let path = s;
    if (s.includes(':')) {
      const parts = s.split(':');
      ns = parts[0] || 'minecraft';
      path = parts.slice(1).join(':');
    }
    return { ns, path };
  }

  async function loadBlockstate(blockName) {
    return await fetchJsonTry(assetPaths(`assets/minecraft/blockstates/${blockName}.json`));
  }

  async function loadModel(modelName) {
    // modelName like "minecraft:block/oak_stairs" OR "block/oak_stairs"
    let m = modelName;
    if (!m.includes(':')) m = 'minecraft:' + m;
    const { ns, path } = parseNsPath(m);
    return await fetchJsonTry(assetPaths(`assets/${ns}/models/${path}.json`));
  }

  function propsToKey(props) {
    const keys = Object.keys(props || {}).sort();
    return keys.map(k => `${k}=${props[k]}`).join(',');
  }

  function variantMatch(variantKey, props) {
    if (!variantKey) return propsToKey(props) === '';
    const want = variantKey.split(',').filter(Boolean);
    for (const cond of want) {
      const [k,v] = cond.split('=');
      if ((props || {})[k] !== v) return false;
    }
    return true;
  }

  function pickVariant(blockstateJson, props) {
    if (blockstateJson.variants) {
      for (const [k, v] of Object.entries(blockstateJson.variants)) {
        if (variantMatch(k, props)) {
          return Array.isArray(v) ? v[0] : v;
        }
      }
      const first = Object.values(blockstateJson.variants)[0];
      return Array.isArray(first) ? first[0] : first;
    }
    return null; // multipart not implemented yet
  }

  function resolveTextureRef(ref, textures) {
    // ref can be "#key" or "minecraft:block/oak_planks"
    if (!ref) return null;
    if (ref.startsWith('#')) {
      const key = ref.slice(1);
      const v = textures?.[key];
      if (!v) return null;
      return resolveTextureRef(v, textures);
    }
    return ref;
  }

  function textureRefToRelPath(ref) {
    // "minecraft:block/oak_planks" -> "assets/minecraft/textures/block/oak_planks.png"
    // "block/oak_planks" -> "assets/minecraft/textures/block/oak_planks.png"
    let r = ref;
    if (!r.includes(':')) r = 'minecraft:' + r;
    const { ns, path } = parseNsPath(r);
    // path typically "block/oak_planks" or "textures/block/oak_planks"
    let p = path;
    if (!p.startsWith('textures/')) p = `textures/${p}`;
    if (!p.endsWith('.png')) p = p + '.png';
    return `assets/${ns}/${p}`;
  }

  const texLoader = new THREE.TextureLoader();
  const textureCache = new Map(); // relPath -> Texture

  async function loadTexture(relAssetPath) {
    if (textureCache.has(relAssetPath)) return textureCache.get(relAssetPath);

    const chosen = await existsTry(assetPaths(relAssetPath));
    if (!chosen) return null;

    const tex = await new Promise((resolve) => {
      texLoader.load(chosen, (t) => resolve(t), undefined, () => resolve(null));
    });

    if (!tex) return null;
    tex.magFilter = THREE.NearestFilter;
    tex.minFilter = THREE.NearestMipmapNearestFilter;
    tex.anisotropy = 1;
    tex.colorSpace = THREE.SRGBColorSpace;
    textureCache.set(relAssetPath, tex);
    return tex;
  }

  // ---- Model element geometry ----

  function uvTo01(uv) {
    // Minecraft UV is 0..16, flip V
    return [uv[0]/16, 1-uv[1]/16, uv[2]/16, 1-uv[3]/16];
  }

  function applyUvRotation(uv, rot) {
    if (!rot) return uv;
    const [u0,v0,u1,v1] = uv;
    const cx = (u0+u1)/2, cy = (v0+v1)/2;
    const pts = [[u0,v0],[u1,v0],[u1,v1],[u0,v1]].map(([u,v]) => [u-cx, v-cy]);

    const ang = (Math.PI/180)*rot;
    const c = Math.cos(ang), s = Math.sin(ang);
    const rpts = pts.map(([u,v]) => [u*c - v*s, u*s + v*c]).map(([u,v]) => [u+cx, v+cy]);

    const us = rpts.map(p=>p[0]), vs = rpts.map(p=>p[1]);
    return [Math.min(...us), Math.min(...vs), Math.max(...us), Math.max(...vs)];
  }

  function rotatePoint(p, origin, axis, angleDeg) {
    const a = angleDeg * Math.PI/180;
    const x = p.x - origin.x, y = p.y - origin.y, z = p.z - origin.z;
    let rx=x, ry=y, rz=z;
    const c=Math.cos(a), s=Math.sin(a);
    if (axis === 'x') { ry = y*c - z*s; rz = y*s + z*c; }
    if (axis === 'y') { rx = x*c + z*s; rz = -x*s + z*c; }
    if (axis === 'z') { rx = x*c - y*s; ry = x*s + y*c; }
    p.x = rx + origin.x; p.y = ry + origin.y; p.z = rz + origin.z;
  }

  function pushTri(out, p, n, uv) {
    out.pos.push(p.x,p.y,p.z);
    out.nrm.push(n.x,n.y,n.z);
    out.uv.push(uv[0], uv[1]);
  }

  function pushQuad(out, a,b,c,d, n, uvRect) {
    const [u0,v0,u1,v1] = uvRect;
    const uvA=[u0,v0], uvB=[u1,v0], uvC=[u1,v1], uvD=[u0,v1];

    pushTri(out, a, n, uvA); pushTri(out, b, n, uvB); pushTri(out, c, n, uvC);
    pushTri(out, a, n, uvA); pushTri(out, c, n, uvC); pushTri(out, d, n, uvD);
  }

  function buildGeometryBuckets(elements) {
    // returns Map(textureKey -> {pos,nrm,uv})
    const buckets = new Map();

    function bucket(key) {
      if (!buckets.has(key)) buckets.set(key, {pos:[], nrm:[], uv:[]});
      return buckets.get(key);
    }

    for (const el of (elements||[])) {
      const f = el.from || [0,0,0];
      const t = el.to || [16,16,16];

      const v = {
        nwn: new THREE.Vector3(f[0]/16, t[1]/16, f[2]/16),
        nen: new THREE.Vector3(t[0]/16, t[1]/16, f[2]/16),
        sen: new THREE.Vector3(t[0]/16, f[1]/16, f[2]/16),
        swn: new THREE.Vector3(f[0]/16, f[1]/16, f[2]/16),

        nwp: new THREE.Vector3(f[0]/16, t[1]/16, t[2]/16),
        nep: new THREE.Vector3(t[0]/16, t[1]/16, t[2]/16),
        sep: new THREE.Vector3(t[0]/16, f[1]/16, t[2]/16),
        swp: new THREE.Vector3(f[0]/16, f[1]/16, t[2]/16),
      };

      if (el.rotation) {
        const o = el.rotation.origin || [8,8,8];
        const origin = new THREE.Vector3(o[0]/16, o[1]/16, o[2]/16);
        const axis = el.rotation.axis;
        const angle = el.rotation.angle || 0;
        for (const k of Object.keys(v)) rotatePoint(v[k], origin, axis, angle);
      }

      const faces = el.faces || {};
      for (const [faceName, face] of Object.entries(faces)) {
        if (!face) continue;
        const texKey = face.texture || '';
        const out = bucket(texKey);

        let uv = face.uv;
        if (!uv) {
          if (faceName === 'north') uv = [f[0], 16-t[1], t[0], 16-f[1]];
          if (faceName === 'south') uv = [f[0], 16-t[1], t[0], 16-f[1]];
          if (faceName === 'west')  uv = [f[2], 16-t[1], t[2], 16-f[1]];
          if (faceName === 'east')  uv = [f[2], 16-t[1], t[2], 16-f[1]];
          if (faceName === 'up')    uv = [f[0], f[2], t[0], t[2]];
          if (faceName === 'down')  uv = [f[0], f[2], t[0], t[2]];
        }

        let uv01 = uvTo01(uv);
        uv01 = applyUvRotation(uv01, face.rotation || 0);

        if (faceName === 'north') pushQuad(out, v.nwn, v.nen, v.sen, v.swn, new THREE.Vector3(0,0,-1), uv01);
        if (faceName === 'south') pushQuad(out, v.nep, v.nwp, v.swp, v.sep, new THREE.Vector3(0,0, 1), uv01);
        if (faceName === 'west')  pushQuad(out, v.nwp, v.nwn, v.swn, v.swp, new THREE.Vector3(-1,0,0), uv01);
        if (faceName === 'east')  pushQuad(out, v.nen, v.nep, v.sep, v.sen, new THREE.Vector3( 1,0,0), uv01);
        if (faceName === 'up')    pushQuad(out, v.nwp, v.nep, v.nen, v.nwn, new THREE.Vector3(0, 1,0), uv01);
        if (faceName === 'down')  pushQuad(out, v.swn, v.sen, v.sep, v.swp, new THREE.Vector3(0,-1,0), uv01);
      }
    }

    return buckets;
  }

  async function buildGeometryAndMaterials(modelJson) {
    const textures = modelJson.textures || {};
    const elements = modelJson.elements || [];

    const buckets = buildGeometryBuckets(elements);

    const geom = new THREE.BufferGeometry();
    const materials = [];
    let cursor = 0;

    // deterministic order
    const keys = Array.from(buckets.keys()).sort();
    const posAll = [];
    const nrmAll = [];
    const uvAll = [];

    for (const key of keys) {
      const b = buckets.get(key);
      const texRef = resolveTextureRef(key, textures);
      const relAsset = texRef ? textureRefToRelPath(texRef) : null;
      const tex = relAsset ? await loadTexture(relAsset) : null;

      const mat = tex
        ? new THREE.MeshStandardMaterial({ map: tex, transparent: true })
        : new THREE.MeshStandardMaterial({ color: 0x888888 });

      materials.push(mat);

      const start = cursor;
      const count = b.pos.length / 3;
      geom.addGroup(start, count, materials.length - 1);

      posAll.push(...b.pos);
      nrmAll.push(...b.nrm);
      uvAll.push(...b.uv);
      cursor += count;
    }

    geom.setAttribute('position', new THREE.Float32BufferAttribute(posAll, 3));
    geom.setAttribute('normal', new THREE.Float32BufferAttribute(nrmAll, 3));
    geom.setAttribute('uv', new THREE.Float32BufferAttribute(uvAll, 2));
    geom.computeBoundingSphere();

    return { geom, materials };
  }

  async function resolveModelWithParents(modelName) {
    const root = await loadModel(modelName);

    let textures = {...(root.textures || {})};
    let elements = root.elements;
    let current = root;

    const seen = new Set();
    while (current.parent) {
      if (seen.has(current.parent)) break;
      seen.add(current.parent);

      const parent = await loadModel(current.parent);
      textures = {...(parent.textures || {}), ...textures};

      if (!elements && parent.elements) elements = parent.elements;
      current = parent;
    }

    return {
      textures,
      elements: elements || [],
    };
  }

  function colorFromString(s) {
    let h = 0;
    for (let i=0;i<s.length;i++) h = (h*31 + s.charCodeAt(i)) >>> 0;
    const r = 80 + (h & 0x7F);
    const g = 80 + ((h >> 8) & 0x7F);
    const b = 80 + ((h >> 16) & 0x7F);
    return (r<<16) | (g<<8) | b;
  }

  async function buildPaletteMesh(paletteIndex, entry, positions, neededCount) {
    const blockId = entry.id;
    const props = entry.props || {};
    const namePart = blockId.includes(':') ? blockId.split(':')[1] : blockId;

    // load blockstate + choose variant
    let variant = null;
    try {
      const bs = await loadBlockstate(namePart);
      variant = pickVariant(bs, props);
    } catch {}

    let modelName = null;
    let rotX = 0, rotY = 0;
    if (variant && variant.model) {
      modelName = variant.model;
      rotX = variant.x || 0;
      rotY = variant.y || 0;
    }

    let geom = null;
    let mats = null;

    if (modelName) {
      try {
        const resolved = await resolveModelWithParents(modelName);
        const built = await buildGeometryAndMaterials({ textures: resolved.textures, elements: resolved.elements });
        geom = built.geom;
        mats = built.materials;
      } catch {}
    }

    // fallback: textured cube
    if (!geom) {
      geom = new THREE.BoxGeometry(1,1,1);
      const texRel = `assets/minecraft/textures/block/${namePart}.png`;
      const tex = await loadTexture(texRel);
      mats = tex ? [new THREE.MeshStandardMaterial({ map: tex, transparent: true })]
                 : [new THREE.MeshStandardMaterial({ color: colorFromString(blockId) })];
    }

    const mesh = new THREE.InstancedMesh(geom, mats.length === 1 ? mats[0] : mats, positions.length);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);

    const dummy = new THREE.Object3D();
    // convert model coords: our model is 0..1 within block. We'll shift by -0.5 so block centers on integer grid.
    for (let i=0;i<positions.length;i++) {
      const p = positions[i];
      dummy.position.set(p[0], p[1], p[2]);
      dummy.rotation.set(0,0,0);
      if (rotY) dummy.rotation.y = (rotY * Math.PI/180);
      if (rotX) dummy.rotation.x = (rotX * Math.PI/180);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    }

    mesh.userData.paletteIndex = paletteIndex;
    mesh.userData.blockId = blockId;
    mesh.userData.props = props;
    mesh.userData.needed = neededCount || positions.length;
    mesh.userData.positions = positions;

    scene.add(mesh);
    return mesh;
  }

  async function applyDisabledToMeshes() {
    let disabled;
    try { disabled = await fetchJsonTry(['disabled.json']); }
    catch { disabled = {disabled_types:[], disabled_positions:[]}; }

    const disabledTypes = new Set(disabled.disabled_types || []);
    const disabledPos = new Set((disabled.disabled_positions || []).map(p => p.join(',')));

    const dummy = new THREE.Object3D();

    for (const item of meshes) {
      const m = item.mesh;
      const blockKey = `${item.blockId}|${JSON.stringify(item.props||{})}`;
      const posArr = item.positions;

      for (let i=0;i<posArr.length;i++) {
        const key = posArr[i].join(',');
        const hide = disabledTypes.has(blockKey) || disabledPos.has(key);

        if (hide) {
          dummy.position.set(0,0,0);
          dummy.scale.set(0,0,0);
        } else {
          dummy.position.set(posArr[i][0], posArr[i][1], posArr[i][2]);
          dummy.scale.set(1,1,1);
        }
        dummy.updateMatrix();
        m.setMatrixAt(i, dummy.matrix);
      }
      m.instanceMatrix.needsUpdate = true;
    }
  }

  function init() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x111111);

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 8000);
    camera.position.set(50, 50, 50);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    document.body.appendChild(renderer.domElement);

    controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 20, 0);
    controls.update();

    raycaster = new THREE.Raycaster();

    const light1 = new THREE.DirectionalLight(0xffffff, 0.9);
    light1.position.set(1,2,1);
    scene.add(light1);
    scene.add(new THREE.AmbientLight(0xffffff, 0.35));

    window.addEventListener('resize', () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    });

    renderer.domElement.addEventListener('click', onClick);

    animate();
  }

  async function loadRender() {
    const data = await fetchJsonTry(['render.json']);
    const palette = data.palette || [];
    const blocks = data.blocks || [];

    // group positions by palette index
    const positionsByP = new Map();
    for (const b of blocks) {
      const p = b.p;
      if (!positionsByP.has(p)) positionsByP.set(p, []);
      positionsByP.get(p).push([b.x, b.y, b.z]);
    }

    // camera centering
    let minX=Infinity, minY=Infinity, minZ=Infinity, maxX=-Infinity, maxY=-Infinity, maxZ=-Infinity;
    for (const arr of positionsByP.values()) {
      for (const p of arr) {
        minX=Math.min(minX,p[0]); minY=Math.min(minY,p[1]); minZ=Math.min(minZ,p[2]);
        maxX=Math.max(maxX,p[0]); maxY=Math.max(maxY,p[1]); maxZ=Math.max(maxZ,p[2]);
      }
    }
    if (isFinite(minX)) {
      const cx=(minX+maxX)/2, cy=(minY+maxY)/2, cz=(minZ+maxZ)/2;
      controls.target.set(cx, cy, cz);
      camera.position.set(cx+60, cy+60, cz+60);
      controls.update();
    }

    // build meshes
    for (let i=0;i<palette.length;i++) {
      const entry = palette[i];
      const positions = positionsByP.get(i) || [];
      if (!positions.length) continue;
      const mesh = await buildPaletteMesh(i, entry, positions, null);
      meshes.push({ mesh, paletteIndex: i, blockId: entry.id, props: entry.props || {}, positions });
    }

    await applyDisabledToMeshes();
    info.textContent = 'Click a block to inspect.';
  }

  async function onClick(ev) {
    const rect = renderer.domElement.getBoundingClientRect();
    const mouse = new THREE.Vector2(
      ((ev.clientX - rect.left) / rect.width) * 2 - 1,
      -(((ev.clientY - rect.top) / rect.height) * 2 - 1)
    );

    raycaster.setFromCamera(mouse, camera);
    const hit = raycaster.intersectObjects(meshes.map(m => m.mesh), true)[0];
    if (!hit) return;

    const mesh = hit.object;
    const instanceId = hit.instanceId;

    // find metadata holder
    const meta = meshes.find(m => m.mesh === mesh);
    if (!meta) return;

    const pos = meta.positions[instanceId];
    lastHit = { blockId: meta.blockId, props: meta.props, pos };

    info.innerHTML = `<b>${meta.blockId}</b><br/>Props: ${JSON.stringify(meta.props)}<br/>Pos: [${pos.join(', ')}]`;
    btnThis.disabled = false;
    btnType.disabled = false;

    if (window.pywebview?.api?.on_block_clicked) {
      try {
        await window.pywebview.api.on_block_clicked(meta.blockId, pos[0], pos[1], pos[2], meta.positions.length);
      } catch {}
    }
  }

  btnThis.addEventListener('click', async () => {
    if (!lastHit) return;
    if (window.pywebview?.api?.disable_this) {
      await window.pywebview.api.disable_this();
    }
    await applyDisabledToMeshes();
  });

  btnType.addEventListener('click', async () => {
    if (!lastHit) return;
    if (window.pywebview?.api?.disable_all_of_type) {
      await window.pywebview.api.disable_all_of_type();
    }
    await applyDisabledToMeshes();
  });

  function animate() {
    requestAnimationFrame(animate);
    renderer.render(scene, camera);
  }

  init();
  loadRender().catch(e => {
    console.error(e);
    info.textContent = 'Failed to load render. See console.';
  });

  </script>
</body>
</html>
"""



# -------------------------
# PATH HELPERS
# -------------------------

def resource_path(relative: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), relative)


DATA_FILE = resource_path("records.json")
LOGO_FILE = resource_path("merchants_guild_logo.png")
BAKKO_SOUND = resource_path("bakkosound.wav")

# Default resource pack (shipped as data)
BUNDLED_DEFAULT_PACK = resource_path("Bare Bones 1.21.11.zip")
DEFAULT_PACK_FILENAME = "default_pack.zip"
SETTINGS_FILENAME = "settings.json"


# -------------------------
# SMALL HELPERS
# -------------------------

def parse_version(v: str):
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0,)


def app_base_dir() -> str:
    if sys.platform == "win32":
        parent = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        parent = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        parent = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))
    base = os.path.join(parent, "MerchantsGuildStorageLocator")
    os.makedirs(base, exist_ok=True)
    return base

def settings_path() -> str:
    return os.path.join(app_base_dir(), SETTINGS_FILENAME)


def load_settings() -> dict:
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # Portable app: if folder is not writable, silently ignore
        pass


def ensure_default_pack_present() -> str | None:
    """
    Ensures resources/default_pack.zip exists next to the app.
    Returns the path, or None if not available.
    """
    base = app_base_dir()
    resources_dir = os.path.join(base, "resources")
    os.makedirs(resources_dir, exist_ok=True)

    target = os.path.join(resources_dir, DEFAULT_PACK_FILENAME)

    if os.path.exists(target):
        return target

    # If bundled pack exists (when running from source or PyInstaller --add-data), copy it.
    try:
        if os.path.exists(BUNDLED_DEFAULT_PACK):
            import shutil
            shutil.copyfile(BUNDLED_DEFAULT_PACK, target)
            return target
    except Exception:
        pass

    return None


def get_selected_pack_path() -> str | None:
    s = load_settings()
    p = s.get("resource_pack")
    if p and os.path.exists(p):
        return p

    # Default to shipped pack
    default_p = ensure_default_pack_present()
    if default_p:
        s["resource_pack"] = default_p
        save_settings(s)
        return default_p

    return None


def extract_pack_assets(pack_zip: str, bundle_dir: str) -> None:
    """
    Extract needed resource-pack assets into the viewer bundle:

      <bundle_dir>/textures/block/*.png
      <bundle_dir>/blockstates/*.json
      <bundle_dir>/models/block/*.json

    This keeps the viewer same-origin (served by pywebview's local http server).
    """
    import zipfile

    tex_out = os.path.join(bundle_dir, "textures", "block")
    bs_out = os.path.join(bundle_dir, "blockstates")
    model_out = os.path.join(bundle_dir, "models", "block")

    os.makedirs(tex_out, exist_ok=True)
    os.makedirs(bs_out, exist_ok=True)
    os.makedirs(model_out, exist_ok=True)

    tex_prefix = "assets/minecraft/textures/block/"
    bs_prefix = "assets/minecraft/blockstates/"
    model_prefix = "assets/minecraft/models/block/"

    with zipfile.ZipFile(pack_zip, "r") as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue

            lower = name.lower()

            if name.startswith(tex_prefix) and lower.endswith(".png"):
                rel = name[len(tex_prefix):]
                out_path = os.path.join(tex_out, rel)
            elif name.startswith(bs_prefix) and lower.endswith(".json"):
                rel = name[len(bs_prefix):]
                out_path = os.path.join(bs_out, rel)
            elif name.startswith(model_prefix) and lower.endswith(".json"):
                rel = name[len(model_prefix):]
                out_path = os.path.join(model_out, rel)
            else:
                continue

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with zf.open(info, "r") as src, open(out_path, "wb") as dst:
                dst.write(src.read())


def get_default_minecraft_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", ""), ".minecraft")
    elif sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "minecraft")
    else:
        return os.path.join(os.path.expanduser("~"), ".minecraft")

def get_default_vanilla_jar(version: str = "1.21.1") -> str:
    mc = get_default_minecraft_dir()
    return os.path.join(mc, "versions", version, f"{version}.jar")

def extract_vanilla_assets_if_needed(jar_path: str, bundle_dir: str) -> None:
    """
    Extract vanilla assets (blockstates/models/textures) into:
      <bundle_dir>/assets_vanilla/assets/minecraft/...

    This gives us complete models for all blocks. Resource-pack overrides are applied via assets_pack first.
    """
    import zipfile

    out_root = os.path.join(bundle_dir, "assets_vanilla")
    os.makedirs(out_root, exist_ok=True)
    marker = os.path.join(out_root, ".vanilla_extracted")
    if os.path.exists(marker):
        return

    if not os.path.exists(jar_path):
        raise FileNotFoundError(f"Vanilla jar not found:\n{jar_path}")

    wanted_prefixes = [
        "assets/minecraft/blockstates/",
        "assets/minecraft/models/",
        "assets/minecraft/textures/",
    ]

    with zipfile.ZipFile(jar_path, "r") as zf:
        for name in zf.namelist():
            n = name.replace("\\", "/")
            if not any(n.startswith(p) for p in wanted_prefixes):
                continue
            if n.endswith("/"):
                continue
            zf.extract(n, out_root)

    with open(marker, "w", encoding="utf-8") as f:
        f.write("ok")


def ensure_viewer_js(bundle_dir: str):
    """
    Download modern three.js module + OrbitControls module into bundle_dir.
    Also patch OrbitControls to import three from a relative path so it works offline.
    """
    import urllib.request
    import re

    os.makedirs(bundle_dir, exist_ok=True)

    three_candidates = [
        "https://cdn.jsdelivr.net/npm/three@0.161.0/build/three.module.js",
        "https://unpkg.com/three@0.161.0/build/three.module.js",
    ]
    orbit_candidates = [
        "https://cdn.jsdelivr.net/npm/three@0.161.0/examples/jsm/controls/OrbitControls.js",
        "https://unpkg.com/three@0.161.0/examples/jsm/controls/OrbitControls.js",
    ]

    three_path = os.path.join(bundle_dir, "three.module.js")
    orbit_path = os.path.join(bundle_dir, "OrbitControls.js")

    def download_first_ok(candidates, out_path, label):
        errors = []
        for url in candidates:
            try:
                with urllib.request.urlopen(url, timeout=25) as r:
                    data = r.read()
                with open(out_path, "wb") as f:
                    f.write(data)
                return
            except Exception as e:
                errors.append(f"{url} -> {repr(e)}")
        raise RuntimeError(f"{label} download failed:\n" + "\n".join(errors))

    if not os.path.exists(three_path):
        download_first_ok(three_candidates, three_path, "three.module.js")

    if not os.path.exists(orbit_path):
        download_first_ok(orbit_candidates, orbit_path, "OrbitControls.js")

    # Patch OrbitControls to import three from local file instead of bare specifier "three"
    try:
        with open(orbit_path, "r", encoding="utf-8") as f:
            js = f.read()
        js2 = re.sub(r"from\s+['\"]three['\"]", "from './three.module.js'", js)
        if js2 != js:
            with open(orbit_path, "w", encoding="utf-8") as f:
                f.write(js2)
    except Exception:
        pass

def titleish(s: str) -> str:
    s = s.strip()
    return s[:1].upper() + s[1:] if s else s

def tower_sort_key(tower: str) -> int:
    return TOWER_ORDER.index(tower) if tower in TOWER_ORDER else 999

def nice_block_name(block_id: str) -> str:
    # minecraft:oak_planks -> Oak Planks
    if ":" in block_id:
        _, name = block_id.split(":", 1)
    else:
        name = block_id
    return name.replace("_", " ").title()

def canonicalize_query(raw: str) -> tuple[str, str | None]:
    """
    Returns:
      (canonical_storage_name, display_prefix_or_None)

    "spruce sign" -> ("Signs", "Spruce Sign")
    "hanging oak sign" -> ("Signs", "Hanging Oak Sign")
    "red banner" -> ("Banners", "Red Banner")
    """
    raw_clean = raw.strip()
    if not raw_clean:
        return "", None

    lower = raw_clean.lower().strip()

    # "lapis" / "lapis block" etc. -> expand to "lapis lazuli" form
    # Handles: "lapis" -> "Lapis Lazuli", "lapis block" -> "Lapis Lazuli Block",
    #          "lapis ore" -> "Lapis Lazuli Ore", "deepslate lapis ore" -> "Deepslate Lapis Lazuli Ore"
    if "lapis" in lower and "lazuli" not in lower:
        raw_clean = raw_clean.strip()
        # Insert "Lazuli" right after "lapis"/"Lapis"
        import re
        raw_clean = re.sub(r'(?i)(lapis)', r'\1 Lazuli', raw_clean)
        lower = raw_clean.lower()

    if "sign" in lower:
        canonical = "Signs"
        prefix = titleish(raw_clean)
        if prefix.lower() == canonical.lower():
            prefix = None
        return canonical, prefix

    if "banner" in lower:
        canonical = "Banners"
        prefix = titleish(raw_clean)
        if prefix.lower() == canonical.lower():
            prefix = None
        return canonical, prefix

    return titleish(raw_clean), None

def parse_litematica_material_list_txt(path: str) -> dict[str, int]:
    """Parse Litematica material list .txt (ASCII table) into {ItemName: MissingCount}.

    Uses the 'Missing' column. Rows with Missing <= 0 are skipped.
    """
    materials: dict[str, int] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    in_table = False
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if line_stripped.startswith("+"):
            continue

        if line_stripped.startswith("|"):
            parts = [p.strip() for p in line_stripped.strip("|").split("|")]
            # Expect: Item | Total | Missing | Available
            if len(parts) >= 4 and parts[0].lower() == "item" and parts[1].lower() == "total":
                in_table = True
                continue

            if not in_table:
                continue

            # Data row
            if len(parts) < 4:
                continue
            item = parts[0].strip()
            if not item or item.lower() == "item":
                continue

            def to_int(s: str) -> int:
                try:
                    return int(s.strip())
                except Exception:
                    return 0

            missing = to_int(parts[2])
            if missing <= 0:
                continue

            materials[item] = materials.get(item, 0) + missing

    return materials



def parse_int_maybe(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        n = int(s)
        return n if n >= 0 else None
    except Exception:
        return None

def get_position_field(rec: dict) -> int:
    # 1 = front, higher = back
    for key in ("position", "slot", "index", "order", "value"):
        if key in rec:
            try:
                return int(rec[key])
            except Exception:
                pass
    return 9999

def play_bakko_sound():
    if not HAS_WINSOUND:
        return
    try:
        winsound.PlaySound(BAKKO_SOUND, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass

def load_records():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
        # Strip trailing whitespace from item names
        for r in records:
            if "item" in r:
                r["item"] = r["item"].strip()
        return records
    except Exception as e:
        messagebox.showerror("Error", f"Could not load records.json:\n{e}")
        sys.exit(1)

def search_item(records, canonical_name: str):
    q = canonical_name.strip()
    if not q:
        return {"mode": "none", "matches": [], "best_name": None}

    q_lower = q.lower()

    # 1) Exact match
    exact = [r for r in records if r.get("item", "").lower() == q_lower]
    if exact:
        exact.sort(key=lambda r: (r["floor"], r["tower"], r.get("side", ""), get_position_field(r)))
        return {"mode": "exact", "matches": exact, "best_name": exact[0]["item"]}

    # 1b) "X Block" -> try "Block of X" (e.g. "Gold Block" -> "Block of Gold")
    #     Also handles prefixed forms like "Waxed Copper Block" -> "Waxed Block of Copper"
    if q_lower.endswith(" block"):
        words = q.rsplit(" ", 1)[0]  # everything before "Block"
        # Try plain "Block of X" first
        candidates = [f"Block of {words}"]
        # Also try moving known prefixes before "Block of"
        # e.g. "Waxed Copper" -> "Waxed Block of Copper"
        word_list = words.split()
        if len(word_list) >= 2:
            for i in range(1, len(word_list)):
                prefix_part = " ".join(word_list[:i])
                rest_part = " ".join(word_list[i:])
                candidates.append(f"{prefix_part} Block of {rest_part}")
        for alt in candidates:
            alt_lower = alt.lower()
            exact2 = [r for r in records if r.get("item", "").lower() == alt_lower]
            if exact2:
                exact2.sort(key=lambda r: (r["floor"], r["tower"], r.get("side", ""), get_position_field(r)))
                return {"mode": "exact", "matches": exact2, "best_name": exact2[0]["item"]}

    names = sorted({r.get("item", "") for r in records if r.get("item")})

    # 2) Substring match — if query is contained in exactly one item name, use it
    substring_hits = [n for n in names if q_lower in n.lower()]
    if len(substring_hits) == 1:
        best = substring_hits[0]
        matches = [r for r in records if r.get("item") == best]
        matches.sort(key=lambda r: (r["floor"], r["tower"], r.get("side", ""), get_position_field(r)))
        return {"mode": "fuzzy", "matches": matches, "best_name": best}

    # 3) Fuzzy match with a stricter cutoff to avoid wrong corrections
    close = difflib.get_close_matches(canonical_name, names, n=3, cutoff=0.75)

    # If fuzzy found candidates, prefer ones that share key words with the query
    if close:
        q_words = set(q_lower.split())
        def word_overlap(name):
            name_words = set(name.lower().split())
            return len(q_words & name_words)
        close.sort(key=lambda n: (-word_overlap(n), n))
        best = close[0]
        matches = [r for r in records if r.get("item") == best]
        matches.sort(key=lambda r: (r["floor"], r["tower"], r.get("side", ""), get_position_field(r)))
        return {"mode": "fuzzy", "matches": matches, "best_name": best}

    # 4) Fallback: check if any item name contains the query or vice versa
    for name in names:
        if name.lower() in q_lower:
            matches = [r for r in records if r.get("item") == name]
            matches.sort(key=lambda r: (r["floor"], r["tower"], r.get("side", ""), get_position_field(r)))
            return {"mode": "fuzzy", "matches": matches, "best_name": name}

    return {"mode": "none", "matches": [], "best_name": None}


# -------------------------
# VIEWER MODE (pywebview)
# -------------------------

def run_viewer_mode(bundle_dir: str):
    try:
        import webview
        if not hasattr(webview, "create_window"):
            raise ImportError("Wrong 'webview' module imported")
    except Exception:
        messagebox.showerror("3D Viewer", "pywebview is not installed (or a conflicting 'webview' module exists).")
        return

    disabled_path = os.path.join(bundle_dir, "disabled.json")
    html_path = os.path.join(bundle_dir, "viewer.html")

    # Ensure default pack is available (when running viewer directly)
    pack_path = get_selected_pack_path()
    if pack_path:
        try:
            extract_pack_assets(pack_path, bundle_dir)
        except Exception:
            pass

    # Vanilla 1.21.1 assets (models/blockstates/textures) for full block shapes
    try:
        vanilla_jar = get_default_vanilla_jar("1.21.1")
        extract_vanilla_assets_if_needed(vanilla_jar, bundle_dir)
    except Exception as e:
        try:
            messagebox.showwarning("Vanilla Assets", f"Could not load vanilla 1.21.1 assets:\n{e}")
        except Exception:
            pass

    # Ensure disabled.json exists
    try:
        if not os.path.exists(disabled_path):
            with open(disabled_path, "w", encoding="utf-8") as f:
                json.dump({"disabled_types": [], "disabled_positions": []}, f)
    except Exception:
        pass

    class Api:
        def __init__(self):
            self._last_clicked = None  # {"type": str, "pos": [x,y,z], "needed": int}

        def on_block_clicked(self, block_type: str, x: int, y: int, z: int, needed: int):
            self._last_clicked = {"type": block_type, "pos": [int(x), int(y), int(z)], "needed": int(needed)}
            return {"ok": True}

        def disable_this(self):
            if not self._last_clicked:
                return {"ok": False, "error": "Nothing clicked yet."}
            data = self._read_disabled(disabled_path)
            entry = self._last_clicked["pos"]
            if entry not in data["disabled_positions"]:
                data["disabled_positions"].append(entry)
            self._write_disabled(disabled_path, data)
            return {"ok": True}

        def disable_all_of_type(self):
            if not self._last_clicked:
                return {"ok": False, "error": "Nothing clicked yet."}
            data = self._read_disabled(disabled_path)
            t = self._last_clicked["type"]
            if t not in data["disabled_types"]:
                data["disabled_types"].append(t)
            self._write_disabled(disabled_path, data)
            return {"ok": True}

        def get_disabled(self):
            return self._read_disabled(disabled_path)
            

        def _read_disabled(self, path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data.setdefault("disabled_types", [])
                data.setdefault("disabled_positions", [])
                return data
            except Exception:
                return {"disabled_types": [], "disabled_positions": []}

        @staticmethod
        def _write_disabled(path, data):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    api = Api()
    # IMPORTANT: use pywebview local HTTP server so fetch('render.json') works
    webview.create_window("Litematica 3D Viewer", html_path, js_api=api, width=1100, height=750)
    webview.start(http_server=True, debug=False)



# -------------------------
# MAIN APP
# -------------------------

class StorageApp:
    # ── Colour palettes ──────────────────────────────────────
    THEME_DARK = {
        "bg":         "#181a20",
        "bg_card":    "#1e2028",
        "fg":         "#e4e6eb",
        "fg_dim":     "#8b8fa3",
        "accent":     "#5b6eae",
        "accent_hover": "#7082c4",
        "input_bg":   "#23262f",
        "input_fg":   "#e4e6eb",
        "input_bd":   "#33374a",
        "btn_bg":     "#2a2d38",
        "btn_fg":     "#d0d3de",
        "btn_border": "#3a3e50",
        "section_fg": "#8b8fa3",
        "checked_bg": "#1d3a26",
        "checked_fg": "#8ce8a0",
        "error_fg":   "#f07070",
        "drop_bg":    "#1e2028",
        "drop_bd":    "#33374a",
        "separator":  "#2a2d38",
    }
    THEME_LIGHT = {
        "bg":         "#f0f2f5",
        "bg_card":    "#ffffff",
        "fg":         "#1a1c23",
        "fg_dim":     "#6b7084",
        "accent":     "#4a5a9e",
        "accent_hover": "#5d6db5",
        "input_bg":   "#ffffff",
        "input_fg":   "#1a1c23",
        "input_bd":   "#d0d3de",
        "btn_bg":     "#e8eaf0",
        "btn_fg":     "#2a2d38",
        "btn_border": "#c5c8d4",
        "section_fg": "#6b7084",
        "checked_bg": "#d4f5dd",
        "checked_fg": "#1a7a34",
        "error_fg":   "#c44040",
        "drop_bg":    "#ffffff",
        "drop_bd":    "#d0d3de",
        "separator":  "#e0e2e8",
    }

    FONT_FAMILY   = "Segoe UI"
    FONT_MAIN     = ("Segoe UI", 10)
    FONT_SMALL    = ("Segoe UI", 9)
    FONT_HEADING  = ("Segoe UI Semibold", 10)
    FONT_SECTION  = ("Segoe UI Semibold", 9)
    FONT_FOOTER   = ("Segoe UI", 8)
    FONT_INPUT    = ("Consolas", 10)

    def __init__(self, master):
        self.master = master
        master.title("Merchants Guild Storage Locator")
        master.minsize(560, 640)

        self.records = load_records()

        # Viewer / schematic state
        self.last_schematic_render = None
        self.last_viewer_bundle_dir = None
        self.schematic_entries = []

        self.dark_mode = True
        self.current_rows = []
        self.header_lines = []
        self.active_floor_filter = None
        self.active_tower_filter = None

        # ── Menu bar ─────────────────────────────────────────
        menu = tk.Menu(master, bd=0, relief="flat")
        helpmenu = tk.Menu(menu, tearoff=0)
        helpmenu.add_command(label="Check for Updates", command=lambda: self.check_updates(False))
        helpmenu.add_command(label="About", command=self.show_about)
        menu.add_cascade(label="Help", menu=helpmenu)
        master.config(menu=menu)

        # ── Outer container with padding ─────────────────────
        self.main_frame = tk.Frame(master)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(8, 12))

        # ── Banner logo ──────────────────────────────────────
        self.header_label = tk.Label(self.main_frame, bd=0)
        self.header_label.pack(pady=(0, 6))
        self.load_banner()

        # ── Input section label ──────────────────────────────
        self.input_header_frame = tk.Frame(self.main_frame)
        self.input_header_frame.pack(fill=tk.X, pady=(0, 4))

        self.lbl_items = tk.Label(
            self.input_header_frame, text="Items", font=self.FONT_SECTION, anchor="w"
        )
        self.lbl_items.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.lbl_qty = tk.Label(
            self.input_header_frame, text="Qty", font=self.FONT_SECTION, width=8, anchor="w"
        )
        self.lbl_qty.pack(side=tk.LEFT, padx=(10, 0))

        # ── Input area ───────────────────────────────────────
        self.input_frame = tk.Frame(self.main_frame)
        self.input_frame.pack(fill=tk.X, pady=(0, 6))

        self.item_box_frame = tk.Frame(self.input_frame, bd=1, relief="solid")
        self.item_box_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.input_box = tk.Text(
            self.item_box_frame, height=6, width=36, wrap="none", bd=0,
            font=self.FONT_INPUT, padx=8, pady=6, highlightthickness=0
        )
        self.input_box.pack(fill=tk.BOTH, expand=True)

        self.qty_box_frame = tk.Frame(self.input_frame, bd=1, relief="solid")
        self.qty_box_frame.pack(side=tk.LEFT, padx=(10, 0))

        self.qty_box = tk.Text(
            self.qty_box_frame, height=6, width=8, wrap="none", bd=0,
            font=self.FONT_INPUT, padx=8, pady=6, highlightthickness=0
        )
        self.qty_box.pack(fill=tk.BOTH, expand=True)

        # Mousewheel bindings
        self.input_box.bind("<MouseWheel>", self._on_items_wheel)
        self.qty_box.bind("<MouseWheel>", self._on_qty_wheel)

        # ── Primary action buttons ───────────────────────────
        self.top_btn_frame = tk.Frame(self.main_frame)
        self.top_btn_frame.pack(fill=tk.X, pady=(2, 8))

        self.search_button = tk.Button(
            self.top_btn_frame, text="Search", command=self.search,
            font=self.FONT_HEADING, bd=0, padx=20, pady=6, cursor="hand2"
        )
        self.search_button.pack(side=tk.LEFT, padx=(0, 6))

        self.copy_btn = tk.Button(
            self.top_btn_frame, text="Copy", command=self.copy_results,
            font=self.FONT_MAIN, bd=0, padx=14, pady=6, cursor="hand2"
        )
        self.copy_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.clear_btn = tk.Button(
            self.top_btn_frame, text="Clear", command=self.clear,
            font=self.FONT_MAIN, bd=0, padx=14, pady=6, cursor="hand2"
        )
        self.clear_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.theme_btn = tk.Button(
            self.top_btn_frame, text="☀", command=self.toggle_theme,
            font=self.FONT_MAIN, bd=0, padx=10, pady=6, cursor="hand2"
        )
        self.theme_btn.pack(side=tk.RIGHT)

        self.filter_popup_btn = tk.Button(
            self.top_btn_frame, text="Filter", command=self.open_filter_popup,
            font=self.FONT_MAIN, bd=0, padx=14, pady=6, cursor="hand2"
        )
        self.filter_popup_btn.pack(side=tk.RIGHT, padx=(0, 6))

        # ── Separator ────────────────────────────────────────
        self.sep1 = tk.Frame(self.main_frame, height=1)
        self.sep1.pack(fill=tk.X, pady=(0, 8))

        # ── Drop zone ────────────────────────────────────────
        self.drop_label = tk.Label(
            self.main_frame,
            text="Drop .litematic file here  —  or use a button below",
            font=self.FONT_SMALL, bd=1, relief="solid",
            height=2, anchor="center", cursor="hand2"
        )
        self.drop_label.pack(fill=tk.X, pady=(0, 8))

        if HAS_DND and HAS_LITEMAPY:
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind("<<Drop>>", self.on_litematica_drop)
        else:
            self.drop_label.bind("<Button-1>", lambda e: self.load_litematica_materials())

        # ── Tool buttons (compact rows) ──────────────────────
        self.bottom_btn_frame = tk.Frame(self.main_frame)
        self.bottom_btn_frame.pack(fill=tk.X, pady=(0, 6))

        self.btn_row1 = tk.Frame(self.bottom_btn_frame)
        self.btn_row1.pack(fill=tk.X, pady=(0, 4))

        self.litematica_btn = tk.Button(
            self.btn_row1, text="Litematica → Storage", command=self.load_litematica_materials,
            font=self.FONT_SMALL, bd=0, padx=12, pady=4, cursor="hand2"
        )
        self.litematica_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.materials_txt_btn = tk.Button(
            self.btn_row1, text="Materials TXT → Storage", command=self.load_materials_txt,
            font=self.FONT_SMALL, bd=0, padx=12, pady=4, cursor="hand2"
        )
        self.materials_txt_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.bg_btn = tk.Button(
            self.btn_row1, text="Set Background", command=self.set_background,
            font=self.FONT_SMALL, bd=0, padx=12, pady=4, cursor="hand2"
        )
        self.bg_btn.pack(side=tk.LEFT)

        self.btn_row2 = tk.Frame(self.bottom_btn_frame)
        self.btn_row2.pack(fill=tk.X, pady=(0, 4))

        self.pack_btn = tk.Button(
            self.btn_row2, text="Change Resource Pack", command=self.choose_resource_pack,
            font=self.FONT_SMALL, bd=0, padx=12, pady=4, cursor="hand2"
        )
        self.pack_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.viewer_btn = tk.Button(
            self.btn_row2, text="Open 3D Viewer", command=self.open_3d_viewer,
            font=self.FONT_SMALL, bd=0, padx=12, pady=4, cursor="hand2", state=tk.DISABLED
        )
        self.viewer_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.pack_label_var = tk.StringVar(value="")
        self.pack_label = tk.Label(
            self.btn_row2, textvariable=self.pack_label_var,
            font=self.FONT_SMALL, anchor="w"
        )
        self.pack_label.pack(side=tk.LEFT, padx=(6, 0))

        self.refresh_pack_label()

        # GitHub schematic dropdown
        self.schematic_var = tk.StringVar(value="Schematics (GitHub)")
        self.schematic_dropdown = tk.OptionMenu(self.main_frame, self.schematic_var, "Schematics (GitHub)")
        self.schematic_dropdown.config(width=32, font=self.FONT_SMALL, bd=0, highlightthickness=0)
        self.schematic_dropdown.pack(anchor="w", pady=(0, 8))
        self.schematic_dropdown.bind("<Button-1>", self.refresh_schematic_library_event)

        # ── Separator ────────────────────────────────────────
        self.sep2 = tk.Frame(self.main_frame, height=1)
        self.sep2.pack(fill=tk.X, pady=(0, 8))

        # ── Results area ─────────────────────────────────────
        self.results_header_label = tk.Label(
            self.main_frame, text="RESULTS", font=self.FONT_SECTION, anchor="w"
        )
        self.results_header_label.pack(fill=tk.X, pady=(0, 4))

        self.results_container = tk.Frame(self.main_frame, bd=1, relief="solid")
        self.results_container.pack(fill=tk.BOTH, expand=True)

        self.results_canvas = tk.Canvas(
            self.results_container, highlightthickness=0, height=280
        )
        self.results_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)

        self.results_frame = tk.Frame(self.results_canvas)
        self._results_window_id = self.results_canvas.create_window(
            (0, 0), window=self.results_frame, anchor="nw"
        )

        self.results_frame.bind(
            "<Configure>",
            lambda e: self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all"))
        )
        # Keep inner frame as wide as the canvas so labels fill the full width
        self.results_canvas.bind(
            "<Configure>",
            lambda e: self.results_canvas.itemconfig(
                self._results_window_id, width=e.width
            )
        )

        self.results_canvas.bind("<MouseWheel>", self._on_results_wheel)
        self.results_frame.bind("<MouseWheel>", self._on_results_wheel)

        # ── Footer ───────────────────────────────────────────
        self.lbl_footer = tk.Label(
            self.main_frame, text="Made by Lontii  ·  Merchants Guild",
            font=self.FONT_FOOTER, anchor="center"
        )
        self.lbl_footer.pack(pady=(6, 0))

        # ── Finalise ─────────────────────────────────────────
        self.apply_theme()
        self.set_results(["Type an item name above and click Search."], [])
        self.check_updates(True)
        self.master.after(120, lambda: self.input_box.focus_set())

    def open_3d_viewer(self):
        if not self.last_schematic_render:
            messagebox.showinfo("3D Viewer", "Import a litematica file first.")
            return
        try:
            import webview
            if not hasattr(webview, "create_window"):
                raise ImportError("Wrong 'webview' module imported")
        except Exception:
            messagebox.showerror("3D Viewer", "pywebview is not installed (or a conflicting 'webview' module exists).")
            return

        bundle_dir = os.path.join(tempfile.gettempdir(), "mg_storage_viewer")
        os.makedirs(bundle_dir, exist_ok=True)        # Resource pack assets (textures/models/blockstates overrides)
        pack_path = get_selected_pack_path()
        if pack_path:
            try:
                extract_pack_assets(pack_path, bundle_dir)
            except Exception as e:
                messagebox.showwarning("Resource Pack", f"Could not extract assets from resource pack:\n{e}")
        else:
            messagebox.showwarning("Resource Pack", "No resource pack selected. Viewer will use vanilla assets.")

        # Vanilla 1.21.1 assets (models/blockstates/textures) for full block shapes
        try:
            vanilla_jar = get_default_vanilla_jar("1.21.1")
            extract_vanilla_assets_if_needed(vanilla_jar, bundle_dir)
        except Exception as e:
            messagebox.showwarning("Vanilla Assets", f"Could not load vanilla 1.21.1 assets:\n{e}")

        
        try:
            ensure_viewer_js(bundle_dir)
        except Exception as e:
            messagebox.showerror("3D Viewer", f"Could not download viewer scripts:\n{e}")
            return

        render_path = os.path.join(bundle_dir, "render.json")
        disabled_path = os.path.join(bundle_dir, "disabled.json")
        html_path = os.path.join(bundle_dir, "viewer.html")

        try:
            with open(render_path, "w", encoding="utf-8") as f:
                json.dump(self.last_schematic_render, f, ensure_ascii=False)

            # reset disabled each open (change if you want persistent)
            with open(disabled_path, "w", encoding="utf-8") as f:
                json.dump({"disabled_types": [], "disabled_positions": []}, f, ensure_ascii=False, indent=2)

            with open(html_path, "w", encoding="utf-8") as f:
                f.write(VIEWER_HTML)
        except Exception as e:
            messagebox.showerror("3D Viewer", f"Could not prepare viewer files:\n{e}")
            return

        self.last_viewer_bundle_dir = bundle_dir

        # Launch same exe/script in viewer mode
        try:
            # Launch viewer mode
            if getattr(sys, "frozen", False):
                # Running as compiled .exe
                cmd = [sys.executable, "--viewer", bundle_dir]
            else:
                # Running as .py (must pass the script file)
                script_path = os.path.abspath(sys.argv[0])
                cmd = [sys.executable, script_path, "--viewer", bundle_dir]

            subprocess.Popen(cmd)
        except Exception as e:
            messagebox.showerror("3D Viewer", f"Could not launch viewer:\n{e}")



    def _on_items_wheel(self, event):
        self.input_box.yview_scroll(-1 * int(event.delta / 120), "units")
        return "break"

    def _on_qty_wheel(self, event):
        self.qty_box.yview_scroll(-1 * int(event.delta / 120), "units")
        return "break"

    def _on_results_wheel(self, event):
        self.results_canvas.yview_scroll(-1 * int(event.delta / 120), "units")
        return "break"

    # -------------------------
    # Theme / banner
    # -------------------------

    def get_theme(self):
        return self.THEME_DARK if self.dark_mode else self.THEME_LIGHT

    def load_banner(self):
        try:
            img = tk.PhotoImage(file=LOGO_FILE)
            h = img.height()
            if h > 120:
                img = img.subsample(max(1, h // 120))
            self.header_img = img
            self.header_label.config(image=self.header_img)
        except Exception:
            pass

    def set_background(self):
        path = filedialog.askopenfilename(
            title="Select Background Image",
            filetypes=[("Images", "*.png;*.gif"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            img = tk.PhotoImage(file=path)
            h = img.height()
            if h > 120:
                img = img.subsample(max(1, h // 120))
            self.header_img = img
            self.header_label.config(image=self.header_img)
        except Exception as e:
            messagebox.showerror("Error", f"Could not load image:\n{e}")

    def _style_button(self, btn, is_accent=False):
        """Apply consistent button styling."""
        t = self.get_theme()
        if is_accent:
            btn.config(
                bg=t["accent"], fg="#ffffff",
                activebackground=t["accent_hover"], activeforeground="#ffffff"
            )
        else:
            btn.config(
                bg=t["btn_bg"], fg=t["btn_fg"],
                activebackground=t["btn_border"], activeforeground=t["btn_fg"]
            )

    def apply_theme(self):
        t = self.get_theme()
        bg = t["bg"]
        fg = t["fg"]

        self.master.config(bg=bg)
        self.main_frame.config(bg=bg)
        self.header_label.config(bg=bg)
        self.lbl_footer.config(bg=bg, fg=t["fg_dim"])

        # Section labels
        self.input_header_frame.config(bg=bg)
        self.lbl_items.config(bg=bg, fg=t["section_fg"])
        self.lbl_qty.config(bg=bg, fg=t["section_fg"])
        self.results_header_label.config(bg=bg, fg=t["section_fg"])

        # Input boxes
        self.input_frame.config(bg=bg)
        self.item_box_frame.config(bg=t["input_bd"])
        self.qty_box_frame.config(bg=t["input_bd"])
        self.input_box.config(
            bg=t["input_bg"], fg=t["input_fg"], insertbackground=t["input_fg"]
        )
        self.qty_box.config(
            bg=t["input_bg"], fg=t["input_fg"], insertbackground=t["input_fg"]
        )

        # Button containers
        for frame in (self.top_btn_frame, self.bottom_btn_frame,
                      self.btn_row1, self.btn_row2):
            frame.config(bg=bg)

        # Accent button (Search)
        self._style_button(self.search_button, is_accent=True)

        # Regular buttons
        for btn in (self.copy_btn, self.clear_btn, self.filter_popup_btn,
                    self.theme_btn, self.litematica_btn, self.materials_txt_btn,
                    self.bg_btn, self.pack_btn, self.viewer_btn):
            self._style_button(btn)

        # Theme toggle icon
        self.theme_btn.config(text="☀" if self.dark_mode else "☾")

        # Pack label
        self.pack_label.config(bg=bg, fg=t["fg_dim"])

        # Drop zone
        self.drop_label.config(bg=t["drop_bg"], fg=t["fg_dim"], highlightbackground=t["drop_bd"])

        # Separators
        self.sep1.config(bg=t["separator"])
        self.sep2.config(bg=t["separator"])

        # Results area
        self.results_container.config(bg=t["input_bd"])
        self.results_canvas.config(bg=t["bg_card"])
        self.results_frame.config(bg=t["bg_card"])

        # Dropdown
        try:
            self.schematic_dropdown.config(
                bg=t["btn_bg"], fg=t["btn_fg"],
                activebackground=t["btn_border"], activeforeground=t["btn_fg"],
                highlightthickness=0
            )
        except Exception:
            pass

        self.render_results()

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.apply_theme()


    def refresh_pack_label(self):
        p = get_selected_pack_path()
        if p:
            self.pack_label_var.set(f"Resource Pack: {os.path.basename(p)}")
        else:
            self.pack_label_var.set("Resource Pack: (none)")

    def choose_resource_pack(self):
        p = filedialog.askopenfilename(
            title="Select Resource Pack (.zip)",
            filetypes=[("Zip files", "*.zip"), ("All files", "*.*")]
        )
        if not p:
            return
        s = load_settings()
        s["resource_pack"] = p
        save_settings(s)
        self.refresh_pack_label()
        messagebox.showinfo("Resource Pack", f"Selected resource pack:\n{p}")

    # -------------------------
    # About / Updates
    # -------------------------

    def show_about(self):
        messagebox.showinfo(
            "About",
            f"Merchants Guild Storage Locator\n"
            f"Version {CURRENT_VERSION}\n\n"
            f"By Lontii\n"
            f"{REPO_URL}"
        )

    def check_updates(self, silent: bool):
        try:
            with urllib.request.urlopen(VERSION_URL, timeout=3) as r:
                latest = r.read().decode().strip()
        except Exception:
            if not silent:
                messagebox.showinfo("Update Check", "Could not check for updates.")
            return

        if parse_version(latest) > parse_version(CURRENT_VERSION):
            if messagebox.askyesno("Update Available", f"New version available ({latest}). Install now?"):
                self.download_and_install()
        else:
            if not silent:
                messagebox.showinfo("Update Check", "You are up to date.")

    def download_and_install(self):
        try:
            target = os.path.join(tempfile.gettempdir(), INSTALLER_FILENAME)
            with urllib.request.urlopen(DOWNLOAD_URL) as r:
                with open(target, "wb") as f:
                    f.write(r.read())
            os.startfile(target)  # Windows
        except Exception:
            webbrowser.open(REPO_URL + "/releases")

    # -------------------------
    # Filters
    # -------------------------

    def open_filter_popup(self):
        t = self.get_theme()
        win = tk.Toplevel(self.master)
        win.title("Filter Results")
        win.geometry("280x200")
        win.resizable(False, False)
        win.config(bg=t["bg"])

        tk.Label(win, text="Floor:", font=self.FONT_SECTION, bg=t["bg"], fg=t["fg"]).pack(pady=(16, 4))
        floor_var = tk.StringVar()
        floor_entry = tk.Entry(
            win, textvariable=floor_var, width=8,
            font=self.FONT_INPUT, bg=t["input_bg"], fg=t["input_fg"],
            insertbackground=t["input_fg"], bd=1, relief="solid"
        )
        floor_entry.pack()

        tk.Label(win, text="Tower:", font=self.FONT_SECTION, bg=t["bg"], fg=t["fg"]).pack(pady=(12, 4))
        tower_var = tk.StringVar()
        tower_menu = tk.OptionMenu(win, tower_var, "", "North", "East", "South", "West")
        tower_menu.config(
            font=self.FONT_SMALL, bg=t["btn_bg"], fg=t["btn_fg"],
            bd=0, highlightthickness=0
        )
        tower_menu.pack()

        btn_frame = tk.Frame(win, bg=t["bg"])
        btn_frame.pack(pady=(16, 0))

        def apply_filters():
            floor_txt = floor_var.get().strip()
            self.active_floor_filter = int(floor_txt) if floor_txt.isdigit() else None
            tower_txt = tower_var.get().strip()
            self.active_tower_filter = tower_txt if tower_txt else None
            win.destroy()
            self.render_results()

        def clear_filters():
            self.active_floor_filter = None
            self.active_tower_filter = None
            win.destroy()
            self.render_results()

        tk.Button(
            btn_frame, text="Apply", width=10, command=apply_filters,
            font=self.FONT_SMALL, bg=t["accent"], fg="#ffffff", bd=0,
            padx=10, pady=4, cursor="hand2"
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_frame, text="Clear", width=10, command=clear_filters,
            font=self.FONT_SMALL, bg=t["btn_bg"], fg=t["btn_fg"], bd=0,
            padx=10, pady=4, cursor="hand2"
        ).pack(side=tk.LEFT)

    def passes_filter(self, row):
        if self.active_floor_filter is not None and row["floor"] != self.active_floor_filter:
            return False
        if self.active_tower_filter is not None and row["tower"] != self.active_tower_filter:
            return False
        return True

    # -------------------------
    # GitHub schematic dropdown
    # -------------------------

    def refresh_schematic_library_event(self, event):
        self.refresh_schematic_library()

    def refresh_schematic_library(self):
        try:
            with urllib.request.urlopen(SCHEMATICS_API_URL, timeout=5) as r:
                data = r.read().decode("utf-8")
            entries = json.loads(data)
        except Exception as e:
            messagebox.showerror("Error", f"Could not fetch schematics from GitHub:\n{e}")
            return

        self.schematic_entries = []
        menu = self.schematic_dropdown["menu"]
        menu.delete(0, "end")
        self.schematic_var.set("Schematics (GitHub)")

        for entry in entries:
            if entry.get("type") != "file":
                continue
            name = entry.get("name", "")
            if not name.lower().endswith(".litematic"):
                continue
            download_url = entry.get("download_url")
            if not download_url:
                continue
            self.schematic_entries.append({"name": name, "download_url": download_url})

        if not self.schematic_entries:
            messagebox.showinfo("Schematic Library", "No .litematic files found in /schematics.")
            return

        for rec in self.schematic_entries:
            menu.add_command(label=rec["name"], command=lambda n=rec["name"]: self.on_schematic_chosen(n))

    def on_schematic_chosen(self, display_name: str):
        for rec in self.schematic_entries:
            if rec["name"] == display_name:
                self.schematic_var.set(display_name)
                self.load_selected_schematic_from_github(rec)
                return

    def load_selected_schematic_from_github(self, entry):
        if not HAS_LITEMAPY:
            messagebox.showerror("Litematica", "Litemapy is not installed.\nRun: py -m pip install litemapy")
            return

        name = entry["name"]
        url = entry["download_url"]

        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".litematic")
            os.close(fd)
            with urllib.request.urlopen(url, timeout=15) as r:
                with open(tmp_path, "wb") as f:
                    f.write(r.read())
        except Exception as e:
            messagebox.showerror("Error", f"Could not download schematic '{name}':\n{e}")
            return

        self.load_litematica_materials(path=tmp_path)

    # -------------------------
    # Copy / Clear / Search
    # -------------------------

    def clear(self):
        self.input_box.delete("1.0", tk.END)
        self.qty_box.delete("1.0", tk.END)
        self.set_results(["Results will appear here."], [])

    def copy_results(self):
        lines = []
        lines.extend(self.header_lines)
        if self.header_lines:
            lines.append("")

        if self.current_rows:
            filtered = [r for r in self.current_rows if self.passes_filter(r)]
            if filtered:
                filtered_sorted = sorted(
                    filtered,
                    key=lambda r: (r["floor"], tower_sort_key(r["tower"]), r["pos"], r["name"], r["side"])
                )
                prev_floor = None
                prev_tower = None
                for row in filtered_sorted:
                    floor = row["floor"]
                    tower = row["tower"]
                    if floor != prev_floor or tower != prev_tower:
                        lines.append(f"Floor {floor} - Tower {tower}:")
                        prev_floor = floor
                        prev_tower = tower

                    checkbox = "[x]" if row.get("checked") else "[ ]"
                    count_part = f" x{row['count']}" if row.get("count") is not None else ""
                    lines.append(f" {checkbox} {row['name']}{count_part} -> Tower {tower} ({row['side']})")
            else:
                lines.append("No rows match current filter.")

        text = "\n".join(lines) + "\n"
        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        messagebox.showinfo("Copied", "Results copied to clipboard!")

    def search(self):
        item_lines = [l.rstrip("\n") for l in self.input_box.get("1.0", tk.END).splitlines()]
        qty_lines = [l.rstrip("\n") for l in self.qty_box.get("1.0", tk.END).splitlines()]

        max_len = max(len(item_lines), len(qty_lines))
        item_lines += [""] * (max_len - len(item_lines))
        qty_lines += [""] * (max_len - len(qty_lines))

        pairs = []
        for item, qty in zip(item_lines, qty_lines):
            item = item.strip()
            if not item:
                continue
            pairs.append((item, parse_int_maybe(qty)))

        if not pairs:
            self.set_results(["No items entered."], [])
            return

        # Easter egg anywhere in list
        if any(item.lower() in ["bakko", "lilbakko"] for item, _ in pairs):
            play_bakko_sound()

        floors = defaultdict(lambda: defaultdict(list))
        missing = []

        for raw_item, qty in pairs:
            canonical, prefix = canonicalize_query(raw_item)
            res = search_item(self.records, canonical)

            if res["mode"] == "none":
                missing.append(raw_item)
                continue

            best = res["best_name"]
            display_name = f"({prefix}) {best}" if prefix else best

            for r in res["matches"]:
                floors[r["floor"]][r["tower"]].append({
                    "floor": r["floor"],
                    "tower": r["tower"],
                    "side": r.get("side", ""),
                    "name": display_name,
                    "count": qty,
                    "pos": get_position_field(r),
                    "checked": False,
                })

        header = []
        for m in missing:
            header.append(f"❌ '{m}' not found")
        if missing:
            header.append("")

        rows = []
        for floor in sorted(floors.keys()):
            for tower in sorted(floors[floor].keys(), key=tower_sort_key):
                for entry in sorted(floors[floor][tower], key=lambda e: (e["pos"], e["name"], e["side"])):
                    rows.append(entry)

        if not rows and not missing:
            header.append("No locations found for the given items.")

        self.set_results(header, rows)

    # -------------------------
    # Results rendering
    # -------------------------

    def set_results(self, header_lines, rows):
        self.header_lines = header_lines
        self.current_rows = rows
        self.render_results()

    def render_results(self):
        for child in self.results_frame.winfo_children():
            child.destroy()

        t = self.get_theme()
        card_bg = t["bg_card"]
        fg = t["fg"]
        dim = t["fg_dim"]
        checked_bg = t["checked_bg"]
        checked_fg = t["checked_fg"]
        error_fg = t["error_fg"]
        pad_x = 12
        pad_y = 2

        def make_label(**kw):
            lbl = tk.Label(self.results_frame, **kw)
            lbl.bind("<MouseWheel>", self._on_results_wheel)
            return lbl

        # Header lines (errors / info)
        for line in self.header_lines:
            is_error = line.startswith("\u274c")  # ❌
            line_fg = error_fg if is_error else dim
            if line.strip():
                make_label(
                    text=line, anchor="w", bg=card_bg, fg=line_fg,
                    font=self.FONT_SMALL, padx=pad_x, pady=pad_y
                ).pack(fill=tk.X, anchor="w")

        if self.header_lines and any(l.strip() for l in self.header_lines):
            sep = tk.Frame(self.results_frame, height=1, bg=t["separator"])
            sep.pack(fill=tk.X, padx=pad_x, pady=4)
            sep.bind("<MouseWheel>", self._on_results_wheel)

        if not self.current_rows:
            return

        filtered = [(i, r) for i, r in enumerate(self.current_rows) if self.passes_filter(r)]
        if not filtered:
            make_label(
                text="No rows match current filter.", anchor="w",
                bg=card_bg, fg=dim, font=self.FONT_SMALL, padx=pad_x
            ).pack(fill=tk.X)
            return

        filtered.sort(key=lambda ir: (
            ir[1]["floor"],
            tower_sort_key(ir[1]["tower"]),
            ir[1]["pos"],
            ir[1]["name"],
            ir[1]["side"],
        ))

        prev_floor = None
        prev_tower = None

        for idx, row in filtered:
            floor = row["floor"]
            tower = row["tower"]

            if floor != prev_floor or tower != prev_tower:
                if prev_floor is not None:
                    spacer = tk.Frame(self.results_frame, height=6, bg=card_bg)
                    spacer.pack(fill=tk.X)
                    spacer.bind("<MouseWheel>", self._on_results_wheel)

                make_label(
                    text=f"Floor {floor}  ·  {tower} Tower",
                    anchor="w", bg=card_bg, fg=t["accent"],
                    font=self.FONT_HEADING, padx=pad_x
                ).pack(fill=tk.X, anchor="w", pady=(4, 2))
                prev_floor = floor
                prev_tower = tower

            is_checked = row.get("checked", False)
            check_icon = "  ✓  " if is_checked else "      "
            count_part = f"  ×{row['count']}" if row.get("count") is not None else ""
            side_text = f"  ({row['side']})" if row.get("side") else ""
            text_line = f"{check_icon}{row['name']}{count_part}{side_text}"

            row_bg = checked_bg if is_checked else card_bg
            row_fg = checked_fg if is_checked else fg

            lbl_row = make_label(
                text=text_line, anchor="w", bg=row_bg, fg=row_fg,
                font=self.FONT_MAIN, padx=pad_x, pady=pad_y, cursor="hand2"
            )
            lbl_row.pack(fill=tk.X, anchor="w")
            lbl_row.bind("<Button-1>", lambda e, ridx=idx: self.toggle_row_checked(ridx))

    def toggle_row_checked(self, row_index):
        if 0 <= row_index < len(self.current_rows):
            self.current_rows[row_index]["checked"] = not self.current_rows[row_index].get("checked", False)
            self.render_results()

    # -------------------------
    # Litematica import
    # -------------------------

    def on_litematica_drop(self, event):
        data = event.data.strip()
        if data.startswith("{") and data.endswith("}"):
            data = data[1:-1]
        path = data.split()[0]
        self.load_litematica_materials(path=path)

    def load_litematica_materials(self, path=None):
        if not HAS_LITEMAPY:
            messagebox.showerror(
                "Litematica Support Missing",
                "Litemapy is not installed.\n\nRun:  py -m pip install litemapy"
            )
            return

        if path is None:
            path = filedialog.askopenfilename(
                title="Select .litematic file",
                filetypes=[("Litematica schematics", "*.litematic"), ("All files", "*.*")]
            )
            if not path:
                return

        try:
            schem = Schematic.load(path)
        except Exception as e:
            messagebox.showerror("Error", f"Could not read litematic file:\n{e}")
            return

        raw_materials = defaultdict(int)

        # For Level-3 rendering we keep full blockstates (id + properties) in a palette.
        palette_index = {}   # (id, tuple(sorted(props.items()))) -> index
        palette = []         # [{"id":..., "props":{...}, "count": int, "name": str}]
        blocks_compact = []  # [{"p": idx, "x":..,"y":..,"z":..}]

        def _get_props(b):
            for attr in ("properties", "props", "state", "states"):
                v = getattr(b, attr, None)
                if isinstance(v, dict):
                    return v
            return {}

        try:
            for region in schem.regions.values():
                for x, y, z in region.block_positions():
                    block = region[x, y, z]
                    if block.id == "minecraft:air":
                        continue

                    raw_materials[block.id] += 1

                    props = _get_props(block) or {}
                    key = (block.id, tuple(sorted((str(k), str(v)) for k, v in props.items())))
                    pi = palette_index.get(key)
                    if pi is None:
                        pi = len(palette)
                        palette_index[key] = pi
                        palette.append({"id": block.id, "props": dict(props), "count": 0})
                    palette[pi]["count"] += 1
                    blocks_compact.append({"p": pi, "x": int(x), "y": int(y), "z": int(z)})
        except Exception as e:
            messagebox.showerror("Error", f"Failed while counting blocks:\n{e}")
            return

        if not raw_materials:
            self.set_results(["No non-air blocks found in this schematic."], [])
            self.viewer_btn.config(state=tk.DISABLED)
            self.last_schematic_render = None
            return

        # Build render JSON for viewer (palette + compact blocks)
        for entry in palette:
            entry["name"] = nice_block_name(entry["id"])
        self.last_schematic_render = {
            "schematic_name": os.path.basename(path),
            "palette": palette,
            "blocks": blocks_compact
        }

        # Enable viewer now that render data exists
        self.viewer_btn.config(state=tk.NORMAL)

        # Build storage results (counts shown)
        materials_pretty = defaultdict(int)
        for block_id, count in raw_materials.items():
            materials_pretty[nice_block_name(block_id)] += int(count)

        floors = defaultdict(lambda: defaultdict(list))
        missing = []

        for pretty_name, count in sorted(materials_pretty.items()):
            canonical, prefix = canonicalize_query(pretty_name)
            result = search_item(self.records, canonical)

            if result["mode"] == "none":
                missing.append(f"{pretty_name} x{count}")
                continue

            best_name = result["best_name"]
            display_name = f"({prefix}) {best_name}" if prefix else best_name

            seen_locations = set()
            for r in result["matches"]:
                key = (r["floor"], r["tower"], r.get("side", ""))
                if key in seen_locations:
                    continue
                seen_locations.add(key)

                floors[r["floor"]][r["tower"]].append({
                    "floor": r["floor"],
                    "tower": r["tower"],
                    "side": r.get("side", ""),
                    "name": display_name,
                    "count": int(count),
                    "pos": get_position_field(r),
                    "checked": False,
                })

        header = [
            f"Materials for schematic: {os.path.basename(path)}",
            "",
            f"Total non-air blocks: {sum(materials_pretty.values())}",
            "",
        ]

        if missing:
            header.append("Not found in storage (by name):")
            header.extend([f"  - {line}" for line in missing])
            header.append("")

        rows = []
        for floor in sorted(floors.keys()):
            for tower in sorted(floors[floor].keys(), key=tower_sort_key):
                for entry in sorted(floors[floor][tower], key=lambda e: (e["pos"], e["name"], e["side"])):
                    rows.append(entry)

        if not rows and not missing:
            header.append("No schematic materials matched items in storage.")

        self.set_results(header, rows)


    # -------------------------
    # 3D Viewer launcher
    # -------------------------

    def load_materials_txt(self, path=None):
        """Import a Litematica material list .txt and convert it into storage results."""
        if path is None:
            path = filedialog.askopenfilename(
                title="Select Litematica Material List (.txt)",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
            )
            if not path:
                return

        try:
            materials = parse_litematica_material_list_txt(path)  # {name: missing_count}
        except Exception as e:
            messagebox.showerror("Error", f"Could not read material list:\n{e}")
            return

        if not materials:
            self.set_results(["No missing materials found in this .txt (Missing column <= 0)."], [])
            return

        floors = defaultdict(lambda: defaultdict(list))
        missing = []

        total_missing = 0
        for item_name, count in sorted(materials.items()):
            total_missing += int(count)
            canonical, prefix = canonicalize_query(item_name)
            result = search_item(self.records, canonical)

            if result["mode"] == "none":
                missing.append(f"{item_name} x{count}")
                continue

            best_name = result["best_name"]
            display_name = f"({prefix}) {best_name}" if prefix else best_name

            seen_locations = set()
            for r in result["matches"]:
                key = (r["floor"], r["tower"], r.get("side", ""))
                if key in seen_locations:
                    continue
                seen_locations.add(key)

                floors[r["floor"]][r["tower"]].append({
                    "floor": r["floor"],
                    "tower": r["tower"],
                    "side": r.get("side", ""),
                    "name": display_name,
                    "count": int(count),
                    "pos": get_position_field(r),
                    "checked": False,
                })

        header = [
            f"Materials (Missing) from: {os.path.basename(path)}",
            "",
            f"Total missing count: {total_missing}",
            "",
        ]

        if missing:
            header.append("Not found in storage (by name):")
            header.extend([f"  - {line}" for line in missing])
            header.append("")

        rows = []
        for floor in sorted(floors.keys()):
            for tower in sorted(floors[floor].keys(), key=tower_sort_key):
                for entry in sorted(floors[floor][tower], key=lambda e: (e["pos"], e["name"], e["side"])):
                    rows.append(entry)

        if not rows and not missing:
            header.append("No missing materials matched items in storage.")

        self.set_results(header, rows)


def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    # Window icon
    try:
        icon = tk.PhotoImage(file=LOGO_FILE)
        root.iconphoto(True, icon)
    except Exception:
        pass

    StorageApp(root)
    root.mainloop()


def main_entry():
    # Viewer mode
    if len(sys.argv) >= 3 and sys.argv[1] == "--viewer":
        run_viewer_mode(sys.argv[2])
        return

    # Normal app mode
    main()


if __name__ == "__main__":
    main_entry()

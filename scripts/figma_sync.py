#!/usr/bin/env python3
"""
figma_sync.py — sync "design system in code ↔ Figma ↔ Storybook" (after the design system gate, stages 10–13).

The script does not talk to Figma itself. It generates JS for the MCP tool `use_figma` (Plugin API, top-level await,
result via `return`) and parses the JSON that the orchestrator saved from the `use_figma` response.
The source of truth is code: tokens/tokens.json (W3C DTCG), app/src/components/**/<Name>/<Name>.tsx, ds/components.md.

Figma variable model (matches the tokens_build.py convention):
  - collection `primitives` (mode `default`): all top-level groups except semantic*;
  - collection `semantic` (modes `light` [+ `dark` if `semantic-dark` exists]): aliases to primitives only;
  - variable name = path joined with "/": color.gray.900 → color/gray/900, semantic.surface.default → surface/default;
  - types: COLOR (hex / rgb[a] → 0–1), FLOAT (px, ms, numbers; rem×16, s×1000), STRING (font family, shadows, easing…).
    Composite values (typography / shadow objects) are skipped and listed in the report.

Collection names can be overridden for an existing Figma file: --primitives-name / --semantic-name
or in ds/figma-map.json: "collections": {"primitives": "Primitive", "semantic": "Semantic"}.

Subcommands (all take `--root <project>`, default is the template root; outputs go to <root>/.tmp/figma/):
  push-variables-js           → push-variables.js (or -1.js, -2.js… if > ~40 KB: primitives first, then semantic).
                                Idempotent upsert of collections, modes, variables, aliases, scopes, codeSyntax.
                                Returns {created, updated, skipped, errors}. No throw.
  export-js                   → export.js — read-only. Returns {fileKey, variables, components} (format in EXPORT_DOC).
  check --figma <json>        → drift.md + a summary on stdout. Exit code 1 means code ↔ Figma drift.
                                [--only tokens|components]
  init-tokens --figma <json> [--force]
                              — once, for a project whose design system lives only in Figma: Variables → tokens.json (DTCG).
                                After that the source of truth is tokens.json and Figma is a copy (then push / check / pull).
  pull-variables --figma <json> [--apply]
                              — values changed in Figma → tokens.json. Without --apply it only prints a diff.
                                Semantic aliases change only if the alias target was changed in Figma.
                                New Figma variables are not added, only listed.
  map --figma <json> [--file-key K] [--refresh]
                              → ds/figma-map.json (matched by name, manual edits are not overwritten).
  storybook-links             → app/src/stories/figma-links.ts (title → Figma node URL). Exits quietly without a map.
  code-connect [--label React|Storybook]
                              → code-connect.json, an array for `send_code_connect_mappings`.
  fetch-rest [--file-key K] [--out F]
                              → .tmp/figma/rest.json — components and variants via the Figma REST API (for CI, no MCP).
                                The personal token comes from the FIGMA_TOKEN environment variable (set by the user, never put in files).
                                Variables over REST are available only on Enterprise, so there are none; then run
                                `check --figma .tmp/figma/rest.json --only components`.

Project paths (copy for the figma-storybook-sync skill): the default is the product-builder layout; another layout
is set by <root>/figma-sync.json → "paths" (keys are in DEFAULT_PATHS, paths are relative to the project root).

Run: python3 figma_sync.py <subcommand> --root <project path> [...]. Stdlib only;
only fetch-rest uses the network.
"""
import argparse
import json
import os
import urllib.error
import urllib.request
import re
import sys
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent
ALIAS = re.compile(r"^\{([^}]+)\}$")
PRIM, SEM = "primitives", "semantic"
PART_LIMIT = 40_000  # bytes per use_figma call
IGNORE_FIGMA = re.compile(r"^(icons?(/|$)|_|\.)", re.I)  # Figma icons and private components are not checked

DEFAULT_PATHS = {
    "tokens": "tokens/tokens.json",
    "components_doc": "ds/components.md",
    "components_dir": "app/src/components",
    "storybook_index": "app/storybook-static/index.json",
    "figma_links": "app/src/stories/figma-links.ts",
    "figma_map": "ds/figma-map.json",
    "out": ".tmp/figma",
}
COMPONENT_EXT = (".tsx", ".jsx")
_paths = {}


def path_of(root, key):
    """Project path by DEFAULT_PATHS key, honoring <root>/figma-sync.json → "paths"."""
    if root not in _paths:
        cfg = root / "figma-sync.json"
        over = json.loads(cfg.read_text(encoding="utf-8")).get("paths", {}) if cfg.exists() else {}
        _paths[root] = {**DEFAULT_PATHS, **over}
    return root / _paths[root][key]

EXPORT_DOC = """{
  "fileKey": "AbC123" | null,
  "variables": [
    {"collection": "primitives", "mode": "default", "name": "color/gray/900", "type": "COLOR", "value": "#111827"},
    {"collection": "semantic", "mode": "light", "name": "text/primary", "type": "COLOR",
     "aliasOf": {"collection": "primitives", "name": "color/gray/900"}},
    {"collection": "primitives", "mode": "default", "name": "space/4", "type": "FLOAT", "value": 4}
  ],
  "components": [
    {"name": "Button", "nodeId": "12:34", "type": "COMPONENT_SET",
     "variantProps": {"variant": ["primary", "secondary"]}, "page": "UI Kit"}
  ]
}"""


# ───────────────────────── tokens → Figma model ─────────────────────────

def flatten(node, path=()):
    """{('a','b'): {'$value', '$type', ...}} with $type inherited from groups (as in tokens_build.py)."""
    out = {}
    gtype = node.get("$type")
    for k, v in node.items():
        if k.startswith("$") or not isinstance(v, dict):
            continue
        if "$value" in v:
            t = dict(v)
            t.setdefault("$type", gtype)
            out[path + (k,)] = t
        else:
            child = dict(v)
            if gtype and "$type" not in child:
                child["$type"] = gtype
            out.update(flatten(child, path + (k,)))
    return out


def fig_name(path):
    return "/".join(path[1:] if path[0].startswith("semantic") else path)


def css_var(path):
    return "var(--" + "-".join(path[1:] if path[0].startswith("semantic") else path) + ")"


def collection_of(path):
    return SEM if path[0].startswith("semantic") else PRIM


HEX = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
RGB = re.compile(r"^rgba?\(\s*([^)]*)\)$", re.I)


def parse_color(v):
    """→ (r, g, b, a) in 0–1 or None."""
    if isinstance(v, dict):
        if isinstance(v.get("hex"), str):
            c = parse_color(v["hex"])
            return c and (*c[:3], float(v.get("alpha", c[3])))
        comps = v.get("components")
        if isinstance(comps, list) and len(comps) == 3 and v.get("colorSpace", "srgb") == "srgb":
            return (*(float(x) for x in comps), float(v.get("alpha", 1)))
        if all(k in v for k in "rgb"):
            return (float(v["r"]), float(v["g"]), float(v["b"]), float(v.get("a", 1)))
        return None
    if not isinstance(v, str):
        return None
    s = v.strip()
    if s.lower() == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    m = HEX.match(s)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)
    m = RGB.match(s)
    if m:
        parts = [p for p in re.split(r"[\s,/]+", m.group(1)) if p]
        if len(parts) not in (3, 4):
            return None
        def ch(p, alpha=False):
            if p.endswith("%"):
                return float(p[:-1]) / 100
            return float(p) if alpha else float(p) / 255
        return (ch(parts[0]), ch(parts[1]), ch(parts[2]), ch(parts[3], True) if len(parts) == 4 else 1.0)
    return None


def hex_of(rgba, force_alpha=False):
    r, g, b, a = rgba
    h = "#" + "".join(f"{round(x * 255):02X}" for x in (r, g, b))
    return h + (f"{round(a * 255):02X}" if (a < 1 or force_alpha) else "")


UNIT = re.compile(r"^(-?\d*\.?\d+)\s*(px|rem|ms|s|%|em)?$")


def to_float(v):
    """dimension / duration / number → (float, unit) or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v), None
    if isinstance(v, dict) and "value" in v and isinstance(v["value"], (int, float)):
        return float(v["value"]), v.get("unit")
    if isinstance(v, str):
        m = UNIT.match(v.strip())
        if m:
            return float(m.group(1)), m.group(2)
    return None


def num(x):
    x = round(x, 4)
    return int(x) if x == int(x) else x


def first_family(v):
    s = v[0] if isinstance(v, list) and v else v
    s = str(s).split(",")[0].strip()
    return s.strip("'\"")


def to_figma(value, ttype):
    """Token value → (Figma type, value) or (None, skip reason)."""
    if ttype == "color" or (ttype is None and isinstance(value, str) and (HEX.match(value) or RGB.match(value))):
        c = parse_color(value)
        if c is None:
            return None, f"color \"{value}\" not parsed (supported: hex, rgb[a])"
        return "COLOR", {k: round(x, 4) for k, x in zip("rgba", c)}
    if ttype == "fontFamily":
        return "STRING", first_family(value)
    if isinstance(value, bool):
        return "BOOLEAN", value
    if isinstance(value, dict) and not ("value" in value and "unit" in value):
        return None, f"composite value ({ttype or 'object'}): Figma Variables cannot store it"
    if isinstance(value, list):
        if ttype == "cubicBezier" and all(isinstance(x, (int, float)) for x in value):
            return "STRING", "cubic-bezier(" + ", ".join(str(num(float(x))) for x in value) + ")"
        if all(isinstance(x, (str, int, float)) for x in value):
            return "STRING", ", ".join(str(x) for x in value)
        return None, "composite list: Figma Variables cannot store it"
    f = to_float(value)
    if f is not None:
        x, unit = f
        if unit in (None, "px", "ms"):
            return "FLOAT", num(x)
        if unit == "rem":
            return "FLOAT", num(x * 16)
        if unit == "s":
            return "FLOAT", num(x * 1000)
        if unit == "em":
            return "FLOAT", num(x)  # letter-spacing: Figma binds FLOAT only; em is stored as a number, pull returns em
        return "STRING", f"{num(x)}{unit}"  # % — kept as a string, lossless
    return "STRING", str(value)


def scopes_for(col, name, ftype, show_prim_colors=False):
    """Explicit Figma scopes by variable type and name."""
    n = name.lower()
    segs = set(re.split(r"[/\-_.]", n))
    has = lambda *ws: any(w in segs for w in ws)
    if ftype == "COLOR":
        if col == PRIM and not show_prim_colors:
            return []  # color primitives are hidden from pickers; the designer uses semantic
        if has("shadow", "elevation"):
            return ["EFFECT_COLOR"]
        if has("fg") or any(s.startswith("text") for s in segs):
            return ["TEXT_FILL"]
        if has("border", "stroke", "divider", "outline", "focus", "ring"):
            return ["STROKE_COLOR"]
        if has("icon", "chart", "data"):
            return ["SHAPE_FILL", "STROKE_COLOR"]
        if has("surface", "bg", "background", "fill", "overlay", "canvas", "backdrop"):
            return ["FRAME_FILL", "SHAPE_FILL"]
        return ["ALL_FILLS", "STROKE_COLOR"]
    if ftype == "FLOAT":
        if has("radius", "corner", "rounded"):
            return ["CORNER_RADIUS"]
        if has("font", "typography", "type", "text"):
            if has("line") and has("height") or has("leading"):
                return ["LINE_HEIGHT"]
            if has("letter", "tracking"):
                return ["LETTER_SPACING"]
            if has("weight"):
                return ["FONT_WEIGHT"]
            if has("paragraph"):
                return ["PARAGRAPH_SPACING"]
            if has("size"):
                return ["FONT_SIZE"]
            return []
        if has("border", "stroke", "outline"):
            return ["STROKE_FLOAT"]
        if has("opacity", "alpha"):
            return ["OPACITY"]
        if has("blur", "shadow", "elevation"):
            return ["EFFECT_FLOAT"]
        if has("space", "spacing", "gap", "padding", "inset", "gutter", "margin"):
            return ["GAP"]
        if has("size", "density", "width", "height", "icon", "avatar", "control"):
            return ["WIDTH_HEIGHT"]
        return []  # duration, z-index and the like are not bound to layer properties
    if ftype == "STRING":
        if has("family"):
            return ["FONT_FAMILY"]
        if has("font", "typography") and has("style"):
            return ["FONT_STYLE"]
        return []
    return []


def load_tokens(root):
    src = path_of(root, "tokens")
    if not src.exists():
        sys.exit(f"✗ missing {src}")
    return json.loads(src.read_text(encoding="utf-8"))


def build_model(data, show_prim_colors=False):
    """tokens.json → {collections: [{name, modes, vars: [...]}], skipped: [(path, reason)], index}.
    var = {name, type, scopes, description, codeSyntax, values: {mode: {"v": value} | {"a": [col, name]}},
           paths: {mode: path in tokens.json}, fallback: {mode: True if the value is taken from another mode}}."""
    flat = flatten(data)
    by_path = dict(flat)
    skipped = []

    def target(alias_path, seen=()):
        """Path the alias points to (alias chains among primitives are followed, for the type only)."""
        key = tuple(alias_path.split("."))
        if key not in by_path or key in seen:
            return None
        return key

    def ftype_of(path, seen=()):
        tok = by_path[path]
        val = tok["$value"]
        m = ALIAS.match(val) if isinstance(val, str) else None
        if m:
            t = target(m.group(1))
            return ftype_of(t, seen + (path,)) if t and t not in seen else (None, "alias not found")
        return to_figma(val, tok.get("$type"))

    def entry(path):
        tok = by_path[path]
        val = tok["$value"]
        m = ALIAS.match(val) if isinstance(val, str) else None
        ft, fv = ftype_of(path)
        if ft is None:
            return None, fv
        if m:
            t = target(m.group(1))
            return {"type": ft, "a": [collection_of(t), fig_name(t)]}, None
        return {"type": ft, "v": fv}, None

    prims = [p for p in flat if not p[0].startswith("semantic")]
    layers = {p[0] for p in flat if p[0].startswith("semantic")}
    collections = []

    pvars = []
    for p in prims:
        e, why = entry(p)
        if e is None:
            skipped.append((".".join(p), why))
            continue
        name = fig_name(p)
        pvars.append({"name": name, "type": e["type"], "scopes": scopes_for(PRIM, name, e["type"], show_prim_colors),
                      "description": by_path[p].get("$description", ""), "codeSyntax": css_var(p),
                      "values": {"default": {k: e[k] for k in ("v", "a") if k in e}},
                      "paths": {"default": p}, "fallback": {}})
    # aliases inside primitives come after their targets
    pvars.sort(key=lambda v: "a" in v["values"]["default"])
    collections.append({"name": PRIM, "modes": ["default"], "vars": pvars})

    if layers:
        modes = ["light"] + (["dark"] if "semantic-dark" in layers else [])
        layer_of = {"light": "semantic", "dark": "semantic-dark"}
        names = {}
        for mode in modes:
            for p in flat:
                if p[0] == layer_of[mode]:
                    names.setdefault(fig_name(p), {})[mode] = p
        svars = []
        for name, paths in names.items():
            vals, fb, typ, ok = {}, {}, None, True
            for mode in modes:
                p = paths.get(mode) or next(iter(paths.values()))
                if mode not in paths:
                    fb[mode] = True
                e, why = entry(p)
                if e is None:
                    skipped.append((".".join(p), why))
                    ok = False
                    break
                if typ and e["type"] != typ:
                    skipped.append((".".join(p), f"type differs between modes: {typ} / {e['type']}"))
                    ok = False
                    break
                typ = e["type"]
                vals[mode] = {k: e[k] for k in ("v", "a") if k in e}
            if not ok:
                continue
            first = paths.get("light") or next(iter(paths.values()))
            svars.append({"name": name, "type": typ, "scopes": scopes_for(SEM, name, typ),
                          "description": by_path[first].get("$description", ""), "codeSyntax": css_var(first),
                          "values": vals, "paths": {m: paths.get(m, first) for m in modes}, "fallback": fb})
        # semantic → semantic aliases come after their targets
        svars.sort(key=lambda v: any(x.get("a", [PRIM])[0] == SEM for x in v["values"].values()))
        collections.append({"name": SEM, "modes": modes, "vars": svars})

    index = {(c["name"], v["name"]): v for c in collections for v in c["vars"]}
    return {"collections": collections, "skipped": skipped, "index": index,
            "modes": {c["name"]: c["modes"] for c in collections}}


# ───────────────────────── JS for use_figma ─────────────────────────

PUSH_RUNTIME = r"""
const R = { part: DATA.part, created: 0, updated: 0, skipped: 0, errors: [] };
const cols = await figma.variables.getLocalVariableCollectionsAsync();
const colByName = new Map(cols.map(c => [c.name, c]));
const byKey = new Map();
for (const v of await figma.variables.getLocalVariablesAsync()) byKey.set(v.variableCollectionId + '::' + v.name, v);
const modeIds = {};
const near = (a, b) => Math.abs(a - b) < 1e-3;
function same(cur, want, type) {
  if (cur === undefined || cur === null) return false;
  if (want && want.type === 'VARIABLE_ALIAS') return cur.type === 'VARIABLE_ALIAS' && cur.id === want.id;
  if (typeof cur === 'object' && cur.type === 'VARIABLE_ALIAS') return false;
  if (type === 'COLOR') return ['r', 'g', 'b'].every(k => near(cur[k], want[k])) && near(cur.a ?? 1, want.a ?? 1);
  if (type === 'FLOAT') return near(cur, want);
  return cur === want;
}
function findVar(colName, name) { const c = colByName.get(colName); return c ? byKey.get(c.id + '::' + name) : undefined; }
for (const col of DATA.collections) {
  let c = colByName.get(col.name);
  if (!c) { c = figma.variables.createVariableCollection(col.name); colByName.set(col.name, c); }
  modeIds[col.name] = {};
  for (const [i, m] of col.modes.entries()) {
    let mode = c.modes.find(x => x.name === m);
    if (!mode && i === 0 && c.modes.length === 1 && !col.modes.includes(c.modes[0].name)) {
      try { c.renameMode(c.modes[0].modeId, m); mode = c.modes.find(x => x.name === m); }
      catch (e) { R.errors.push('mode ' + col.name + '/' + m + ': ' + e.message); }
    }
    if (!mode) {
      try { mode = { modeId: c.addMode(m), name: m }; }
      catch (e) { R.errors.push('mode ' + col.name + '/' + m + ' (does the plan limit modes?): ' + e.message); }
    }
    if (mode) modeIds[col.name][m] = mode.modeId;
  }
}
for (const col of DATA.collections) {
  const c = colByName.get(col.name);
  for (const d of col.vars) {
    try {
      let v = findVar(col.name, d.n), isNew = false;
      if (v && v.resolvedType !== d.t) {
        R.skipped++; R.errors.push(col.name + '/' + d.n + ': Figma has type ' + v.resolvedType + ', code has ' + d.t + ' (left untouched)');
        continue;
      }
      if (!v) { v = figma.variables.createVariable(d.n, c, d.t); byKey.set(c.id + '::' + d.n, v); isNew = true; }
      let changed = false;
      for (const [m, val] of Object.entries(d.v)) {
        const mid = modeIds[col.name][m];
        if (!mid) continue;
        let want = val.v;
        if (val.a) {
          const t = findVar(val.a[0], val.a[1]);
          if (!t) { R.errors.push(col.name + '/' + d.n + ': alias target not found ' + val.a.join(' → ')); continue; }
          want = figma.variables.createVariableAlias(t);
        }
        if (!same(v.valuesByMode[mid], want, d.t)) { v.setValueForMode(mid, want); changed = true; }
      }
      if (JSON.stringify([...v.scopes].sort()) !== JSON.stringify([...d.s].sort())) { v.scopes = d.s; changed = true; }
      if ((v.description || '') !== (d.d || '')) { v.description = d.d || ''; changed = true; }
      if (d.cs && (v.codeSyntax || {}).WEB !== d.cs) { v.setVariableCodeSyntax('WEB', d.cs); changed = true; }
      if (isNew) R.created++; else if (changed) R.updated++; else R.skipped++;
    } catch (e) { R.errors.push(col.name + '/' + d.n + ': ' + e.message); }
  }
}
return R;
"""

EXPORT_JS = r"""// figma_sync.py export-js — read-only: the file's variables and components. Save the response to .tmp/figma/export.json
// A use_figma response must be ≤ 48 KB: export a large design system in parts (export-variables-*.js, export-components.js → .tmp/figma/export/*.json)
const PART = 'all', ONLY_COLLECTION = null;
const hx = x => Math.round(x * 255).toString(16).padStart(2, '0').toUpperCase();
const hex = c => '#' + hx(c.r) + hx(c.g) + hx(c.b) + ((c.a ?? 1) < 1 ? hx(c.a) : '');
const cols = await figma.variables.getLocalVariableCollectionsAsync();
const colById = new Map(cols.map(c => [c.id, c]));
const vars = await figma.variables.getLocalVariablesAsync();
const varById = new Map(vars.map(v => [v.id, v]));
const variables = [];
for (const c of cols) {
  if (PART === 'components' || (ONLY_COLLECTION && c.name !== ONLY_COLLECTION)) continue;
  for (const id of c.variableIds) {
    const v = varById.get(id);
    if (!v) continue;
    for (const m of c.modes) {
      const val = v.valuesByMode[m.modeId];
      const row = { collection: c.name, mode: m.name, name: v.name, type: v.resolvedType };
      if (val && typeof val === 'object' && val.type === 'VARIABLE_ALIAS') {
        let t = varById.get(val.id);
        if (!t) { try { t = await figma.variables.getVariableByIdAsync(val.id); } catch (e) { t = null; } }
        const tc = t && colById.get(t.variableCollectionId);
        row.aliasOf = t ? { collection: tc ? tc.name : '?remote', name: t.name } : { id: val.id };
      } else if (v.resolvedType === 'COLOR' && val) {
        row.value = hex(val);
      } else {
        row.value = val === undefined ? null : val;
      }
      variables.push(row);
    }
  }
}
// loadAllPagesAsync is forbidden in Figma MCP, so load pages one at a time
const components = [];
const parseVariantName = n => n.split(',').map(s => s.split('=').map(x => x.trim())).filter(p => p.length === 2);
for (const page of (PART === 'variables' ? [] : figma.root.children)) {
  if (page.loadAsync) await page.loadAsync();
  const nodes = page.findAllWithCriteria({ types: ['COMPONENT_SET', 'COMPONENT'] });
  for (const n of nodes) {
    if (n.type === 'COMPONENT' && n.parent && n.parent.type === 'COMPONENT_SET') continue;
    const variantProps = {};
    if (n.type === 'COMPONENT_SET') {
      try {
        for (const [k, d] of Object.entries(n.componentPropertyDefinitions)) {
          if (d.type === 'VARIANT') variantProps[k] = d.variantOptions || [];
        }
      } catch (e) {
        for (const ch of n.children) for (const [k, val] of parseVariantName(ch.name)) {
          (variantProps[k] = variantProps[k] || []).includes(val) || variantProps[k].push(val);
        }
      }
    }
    components.push({ name: n.name, nodeId: n.id, type: n.type, variantProps, page: page.name });
  }
}
let fileKey = null;
try { fileKey = figma.fileKey || null; } catch (e) { fileKey = null; }
return { fileKey, variables, components };
"""


def compact_var(v):
    return {"n": v["name"], "t": v["type"], "s": v["scopes"], "d": v["description"], "cs": v["codeSyntax"],
            "v": v["values"]}


def dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def push_js(data_obj):
    return ("// figma_sync.py push-variables-js — generated, do not edit by hand. Pass the whole file to use_figma.\n"
            f"const DATA = {dumps(data_obj)};\n" + PUSH_RUNTIME)


def push_parts(model, limit=PART_LIMIT):
    """Splits variables into parts of ≤ limit bytes; when splitting, a collection boundary is always a part boundary."""
    whole = {"part": "1/1", "collections": [{"name": c["name"], "modes": c["modes"],
                                             "vars": [compact_var(v) for v in c["vars"]]} for c in model["collections"]]}
    if len(push_js(whole).encode("utf-8")) <= limit:
        return [whole]
    base = len(push_js({"part": "00/00", "collections": []}).encode("utf-8")) + 200
    parts = []
    for c in model["collections"]:
        cur, size = [], base
        for v in c["vars"]:
            cv = compact_var(v)
            s = len(dumps(cv).encode("utf-8")) + 1
            if cur and size + s > limit:
                parts.append({"collections": [{"name": c["name"], "modes": c["modes"], "vars": cur}]})
                cur, size = [], base
            cur.append(cv)
            size += s
        if cur or not c["vars"]:
            parts.append({"collections": [{"name": c["name"], "modes": c["modes"], "vars": cur}]})
    for i, p in enumerate(parts, 1):
        p["part"] = f"{i}/{len(parts)}"
    return [{"part": p["part"], "collections": p["collections"]} for p in parts]


def out_dir(root):
    d = path_of(root, "out")
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_push(args):
    root = args.root
    model = build_model(load_tokens(root), args.show_primitive_colors)
    d = out_dir(root)
    for old in d.glob("push-variables*.js"):
        old.unlink()
    parts = push_parts(model, args.limit)
    files = []
    if len(parts) == 1:
        f = d / "push-variables.js"
        f.write_text(push_js(parts[0]), encoding="utf-8")
        files.append(f)
    else:
        for i, p in enumerate(parts, 1):
            f = d / f"push-variables-{i}.js"
            f.write_text(push_js(p), encoding="utf-8")
            files.append(f)
    counts = {c["name"]: len(c["vars"]) for c in model["collections"]}
    print(f"✓ variables: " + ", ".join(f"{k} {n}" for k, n in counts.items())
          + f"; semantic modes: {', '.join(model['modes'].get(SEM, [])) or '—'}")
    for f in files:
        print(f"  → {f.relative_to(root)} ({f.stat().st_size // 1024} KB)")
    if model["skipped"]:
        print(f"! skipped {len(model['skipped'])} tokens (they do not fit Figma Variables):")
        for p, why in model["skipped"]:
            print(f"  · {p}: {why}")
    print("Next: pass each file to use_figma in order (whole code), then export-js → check.")
    return 0


def cmd_export(args):
    d = out_dir(args.root)
    f = d / "export.js"
    f.write_text(EXPORT_JS, encoding="utf-8")
    head = "const PART = 'all', ONLY_COLLECTION = null;"
    parts = [d / "export-components.js"]
    parts[0].write_text(EXPORT_JS.replace(head, "const PART = 'components', ONLY_COLLECTION = null;"), encoding="utf-8")
    for col in (PRIM, SEM):
        pf = d / f"export-variables-{re.sub(r'[^A-Za-z0-9]+', '-', col).strip('-').lower()}.js"
        pf.write_text(EXPORT_JS.replace(head, f"const PART = 'variables', ONLY_COLLECTION = {json.dumps(col)};"), encoding="utf-8")
        parts.append(pf)
    print(f"✓ {f.relative_to(args.root)} — read-only. Save the use_figma response to .tmp/figma/export.json:")
    print(EXPORT_DOC)
    print("If the response is > 48 KB (the use_figma limit), run the parts one by one, each response in .tmp/figma/export/<part>.json, "
          "then check/map --figma .tmp/figma/export (the folder is merged):")
    for pf in parts:
        print(f"  → {pf.relative_to(args.root)}")
    return 0


# ───────────────────────── parsing the Figma response ─────────────────────────

def find_payload(o):
    """Extracts {variables, components} from a response as is or from an MCP wrapper (content/text/result)."""
    if isinstance(o, str):
        try:
            return find_payload(json.loads(o))
        except ValueError:
            return None
    if isinstance(o, dict):
        if "variables" in o or "components" in o:
            return o
        for v in o.values():
            r = find_payload(v)
            if r is not None:
                return r
    if isinstance(o, list):
        for v in o:
            r = find_payload(v)
            if r is not None:
                return r
    return None


def load_figma(path):
    p = Path(path)
    if not p.exists():
        sys.exit(f"✗ missing {p}: save the use_figma response (export.js) to this file")
    if p.is_dir():  # export in parts (use_figma response limit): merge them
        payload = {"fileKey": None, "variables": [], "components": []}
        for f in sorted(p.glob("*.json")):
            part = find_payload(f.read_text(encoding="utf-8"))
            if part is None:
                continue
            payload["fileKey"] = payload["fileKey"] or part.get("fileKey")
            payload["variables"] += part.get("variables") or []
            payload["components"] += part.get("components") or []
        if not payload["variables"] and not payload["components"]:
            sys.exit(f"✗ no objects with variables / components in {p}/*.json")
    else:
        payload = find_payload(p.read_text(encoding="utf-8"))
    if payload is None:
        sys.exit(f"✗ no object with variables / components in {p}")
    vars_ = {}
    modes = {}
    for r in payload.get("variables") or []:
        key = (r["collection"], r["name"])
        modes.setdefault(r["collection"], [])
        if r["mode"] not in modes[r["collection"]]:
            modes[r["collection"]].append(r["mode"])
        v = vars_.setdefault(key, {"type": r.get("type"), "modes": {}})
        if r.get("aliasOf"):
            a = r["aliasOf"]
            v["modes"][r["mode"]] = {"a": [a.get("collection", "?"), a.get("name", a.get("id", "?"))]}
        else:
            v["modes"][r["mode"]] = {"v": r.get("value")}
    return {"fileKey": payload.get("fileKey"), "vars": vars_, "modes": modes,
            "components": payload.get("components") or []}


def canon(ftype, v):
    """Normal form of a value for comparison."""
    if v is None:
        return None
    if ftype == "COLOR":
        c = parse_color(v)
        return hex_of(c, force_alpha=True) if c else str(v)
    if ftype == "FLOAT":
        try:
            return num(float(v))
        except (TypeError, ValueError):
            return str(v)
    return v


def resolve(vars_, modes, col, name, mode, seen=()):
    """Final (canonical) value with aliases resolved. The alias target is taken in its first mode."""
    v = vars_.get((col, name))
    if v is None or (col, name) in seen:
        return None
    val = v["modes"].get(mode)
    if val is None:
        first = (modes.get(col) or [None])[0]
        val = v["modes"].get(first) if first else None
    if val is None:
        return None
    if "a" in val:
        tc, tn = val["a"]
        tmode = (modes.get(tc) or [mode])[0]
        return resolve(vars_, modes, tc, tn, tmode, seen + ((col, name),))
    return canon(v["type"], val["v"])


def code_vars(model):
    return {k: {"type": v["type"], "modes": v["values"]} for k, v in model["index"].items()}


# ───────────────────────── components: catalog, code, stories ─────────────────────────

PASCAL = re.compile(r"^[A-Z][a-z0-9]*[A-Z0-9]?[A-Za-z0-9]*$")


def doc_components(root):
    """Anchor names from ds/components.md: ###/#### headings (with a number like "5.1 " or without), table rows
    "| **Name** |" (short catalog entries), `<a id="Name">`."""
    f = path_of(root, "components_doc")
    if not f.exists():
        return set()
    names = set()
    comp_table, prev_row = False, False
    for line in f.read_text(encoding="utf-8").splitlines():
        row = line.startswith("|")
        if row and not prev_row:  # table header: "| **Name** |" rows are taken only from component tables
            first = line.strip("|").split("|")[0].strip().lower()
            comp_table = first in ("\u043a\u043e\u043c\u043f\u043e\u043d\u0435\u043d\u0442", "component", "name", "\u0438\u043c\u044f")  # includes the Russian headers "komponent" and "imya" (written as escapes)
        prev_row = row
        for m in re.finditer(r'<a\s+(?:id|name)="([A-Z][A-Za-z0-9]+)"', line):
            names.add(m.group(1))
        m = re.match(r"^#{3,4}\s+(?:\d+(?:\.\d+)*[a-z]?\s+)?`?([A-Za-z][A-Za-z0-9]*)`?(?=$|[\s*(—–:,])(?!\s*→)", line)
        if not m and row and comp_table:
            m = re.match(r"^\|\s*\*\*`?([A-Z][A-Za-z0-9]+)`?\*\*\s*\|", line)
        if m and PASCAL.match(m.group(1)) and re.search(r"[a-z]", m.group(1)):
            names.add(m.group(1))
    return names


def code_components(root):
    """{Name: path to Name.tsx from the root}: folders <components_dir>/**/<Name>/<Name>.tsx (or .jsx)."""
    base = path_of(root, "components_dir")
    out = {}
    if not base.exists():
        return out
    for f in sorted(x for x in base.rglob("*") if x.suffix in COMPONENT_EXT):
        if f.stem == f.parent.name and PASCAL.match(f.stem) and ".stories" not in f.name:
            out.setdefault(f.stem, str(f.relative_to(root)))
    return out


def story_info(root, code):
    """{Name: {title, stories: [names], argOptions: {prop: [values]}, file}} from *.stories.tsx and storybook-static."""
    info = {}
    for name, src in code.items():
        f = next((x for ext in (".tsx", ".jsx", ".ts", ".js")
                  if (x := (root / src).with_name(f"{name}.stories{ext}")).exists()), None)
        if f is None:
            continue
        t = f.read_text(encoding="utf-8")
        m = re.search(r"\btitle\s*:\s*[\"'`]([^\"'`]+)[\"'`]", t)
        opts = {}
        am = re.search(r"\bargTypes\s*:", t)
        if am:
            for pm in re.finditer(r"([A-Za-z_$][\w$]*)\s*:\s*\{[^{}]*?\boptions\s*:\s*\[([^\]]*)\]", t[am.end():]):
                vals = re.findall(r"[\"'`]([^\"'`]+)[\"'`]", pm.group(2))
                if vals:
                    opts.setdefault(pm.group(1), vals)
        stories = []
        for sm in re.finditer(r"export\s+const\s+([A-Z]\w*)\s*(?::\s*\w+)?\s*=\s*\{", t):
            nm = re.search(r"\bname\s*:\s*[\"'`]([^\"'`]+)", t[sm.end():sm.end() + 300])
            stories.append(sm.group(1))
            if nm:
                stories.append(nm.group(1))
        info[name] = {"title": m.group(1) if m else None, "stories": stories, "argOptions": opts,
                      "file": str(f.relative_to(root))}
    idx = path_of(root, "storybook_index")
    if idx.exists():
        try:
            entries = json.loads(idx.read_text(encoding="utf-8")).get("entries", {})
        except ValueError:
            entries = {}
        for e in entries.values():
            if e.get("type") != "story":
                continue
            name = e["title"].split("/")[-1].replace(" ", "")
            i = info.setdefault(name, {"title": e["title"], "stories": [], "argOptions": {}, "file": None})
            i["title"] = i["title"] or e["title"]
            if e.get("name") and e["name"] not in i["stories"]:
                i["stories"].append(e["name"])
    return info


def norm_figma_name(n):
    return n.split("/")[-1].strip().replace(" ", "")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def figma_components(fig, code=None):
    """{Name: Figma component} (COMPONENT_SET preferred), [duplicates], [ignored].
    `Parent/Part` (DataTable/Row, Tabs/Item), when `code` is known, is a part of component Parent, not a separate one."""
    out, dups, ignored, parts = {}, [], [], {}
    for c in fig["components"]:
        if IGNORE_FIGMA.match(c["name"].strip()):
            ignored.append(c["name"])
            continue
        n = norm_figma_name(c["name"])
        if code is not None and "/" in c["name"] and n not in code:
            parent = c["name"].split("/")[0].strip().replace(" ", "")
            if parent in code:
                parts.setdefault(parent, []).append(c)
                continue
        if n in out:
            if out[n]["type"] != "COMPONENT_SET" and c["type"] == "COMPONENT_SET":
                dups.append(out[n]["name"])
                out[n] = c
            else:
                dups.append(c["name"])
            continue
        out[n] = c
    for parent, ps in parts.items():
        if parent not in out:  # Figma has only parts; the link points to the first one (🟡)
            out[parent] = {**ps[0], "name": ps[0]["name"] + " (part)", "variantProps": {}}
    return out, dups, ignored


def load_map(root):
    f = path_of(root, "figma_map")
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


# ───────────────────────── check ─────────────────────────

def fmt_val(x):
    if isinstance(x, dict) and "a" in x:
        return "→ " + "/".join(x["a"])
    if isinstance(x, dict) and "v" in x:
        return str(x["v"])
    return str(x)


def check_tokens(model, fig):
    cv, fv = code_vars(model), fig["vars"]
    cm, fm = model["modes"], fig["modes"]
    drift, info = [], []
    for (col, name), c in cv.items():
        f = fv.get((col, name))
        label = f"{col}/{name}"
        if f is None:
            drift.append(("missing in Figma", label, "", ""))
            continue
        if f["type"] and f["type"] != c["type"]:
            drift.append(("type", label, c["type"], f["type"]))
            continue
        for mode, cval in c["modes"].items():
            if mode not in f["modes"]:
                drift.append(("missing mode", f"{label} [{mode}]", fmt_val(cval), "—"))
                continue
            fval = f["modes"][mode]
            ca, fa = cval.get("a"), fval.get("a")
            if ca and fa:
                if list(ca) != list(fa):
                    drift.append(("alias", f"{label} [{mode}]", "→ " + "/".join(ca), "→ " + "/".join(fa)))
                continue  # same target: the target's value drift is already in its own row
            if ca and not fa:
                drift.append(("alias unbound in Figma", f"{label} [{mode}]", "→ " + "/".join(ca), str(fval.get("v"))))
                continue
            if fa and not ca:
                drift.append(("alias in Figma, value in code", f"{label} [{mode}]", fmt_val(cval), "→ " + "/".join(fa)))
                continue
            a = resolve(cv, cm, col, name, mode)
            b = resolve(fv, fm, col, name, mode)
            if a != b:
                drift.append(("value", f"{label} [{mode}]", str(a), str(b)))
    ours = {PRIM, SEM}
    for (col, name) in fv:
        if col in ours and (col, name) not in cv:
            drift.append(("missing in code", f"{col}/{name}", "", ""))
    other = sorted({col for col, _ in fv if col not in ours})
    if other:
        info.append("Figma collections outside the sync: " + ", ".join(f"`{o}`" for o in other))
    if model["skipped"]:
        info.append(f"Tokens that do not fit Variables ({len(model['skipped'])}): "
                    + ", ".join(f"`{p}`" for p, _ in model["skipped"]))
    return drift, info


def check_components(root, fig):
    doc = doc_components(root)
    code = code_components(root)
    figc, dups, ignored = figma_components(fig, code)
    stories = story_info(root, code)
    drift, info, heur = [], [], []
    for n in sorted(set(code) - set(figc)):
        drift.append(("missing in Figma", n, code[n]))
    for n in sorted(set(figc) - set(code)):
        if n in doc:
            info.append(f"`{n}`: in Figma and the catalog, but no code (catalog without code?)")
        else:
            drift.append(("only in Figma", n, f"{figc[n]['name']} · {figc[n]['nodeId']} · {figc[n].get('page', '')}"))
    if doc:
        for n in sorted(set(doc) - set(code)):
            info.append(f"`{n}`: in ds/components.md, missing in code")
        for n in sorted(set(code) - set(doc)):
            info.append(f"`{n}`: in code, no anchor in ds/components.md (name = anchor!)")
    else:
        info.append("ds/components.md not found or has no anchor headings")
    if dups:
        info.append("Duplicate names in Figma (COMPONENT_SET / first one taken): " + ", ".join(f"`{d}`" for d in dups))
    if ignored:
        info.append(f"Not checked (icons / private `_`, `.`): {len(ignored)}")

    # variants
    for n in sorted(set(code) & set(figc)):
        vp = figc[n].get("variantProps") or {}
        st = stories.get(n, {})
        args = {norm(k): (k, v) for k, v in (st.get("argOptions") or {}).items()}
        snames = [norm(s) for s in st.get("stories", [])]
        for prop, values in vp.items():
            hit = args.get(norm(prop))
            if hit:
                k, cvals = hit
                fa, ca = {norm(x) for x in values}, {norm(x) for x in cvals}
                only_f = [x for x in values if norm(x) not in ca]
                only_c = [x for x in cvals if norm(x) not in fa]
                if only_f or only_c:
                    drift.append(("variants", n, f"{prop}: only Figma {only_f or '—'} · only code ({k}) {only_c or '—'}"))
                continue
            if not snames:
                continue
            miss = [x for x in values if norm(x) not in ("true", "false", "default", "")
                    and not any(norm(x) in s for s in snames)]
            if miss:
                heur.append(f"`{n}` · {prop}: no story with {', '.join(miss)}")
    return drift, info, heur, figc


def cmd_check(args):
    root = args.root
    fig = load_figma(args.figma)
    lines = ["# Code ↔ Figma drift check", "",
             "Generated by `execution/figma_sync.py check`; do not edit by hand. The source of truth is code.", ""]
    total = 0
    summary = []
    if args.only is None and not fig["vars"] and fig["components"]:
        args.only = "components"  # export without Variables (REST below Enterprise): tokens are not checked
        summary.append("tokens not checked: the export has no Variables")
    if args.only in (None, "tokens"):
        model = build_model(load_tokens(root))
        tdrift, tinfo = check_tokens(model, fig)
        total += len(tdrift)
        summary.append(f"tokens: {len(model['index'])} in code, {len(fig['vars'])} in Figma, drift {len(tdrift)}")
        lines += [f"## Tokens — drift {len(tdrift)}", ""]
        if tdrift:
            lines += ["| What | Variable | Code | Figma |", "|---|---|---|---|"]
            lines += [f"| {k} | `{n}` | {a} | {b} |" for k, n, a, b in tdrift]
        else:
            lines.append("Everything matches.")
        lines += [""] + [f"- {i}" for i in tinfo] + [""]
    if args.only in (None, "components"):
        cdrift, cinfo, heur, figc = check_components(root, fig)
        fmap = load_map(root) or {}
        node_ids = {c["nodeId"] for c in fig["components"]}
        broken = [(n, e.get("nodeId")) for n, e in (fmap.get("components") or {}).items()
                  if e.get("nodeId") and e["nodeId"] not in node_ids]
        total += len(cdrift) + len(broken)
        summary.append(f"components: {len(code_components(root))} in code, {len(figc)} in Figma, "
                       f"drift {len(cdrift)}, broken nodeIds in the map {len(broken)}")
        lines += [f"## Components — drift {len(cdrift)}", ""]
        if cdrift:
            lines += ["| What | Component | Details |", "|---|---|---|"]
            lines += [f"| {k} | `{n}` | {d} |" for k, n, d in cdrift]
        else:
            lines.append("Everything matches.")
        lines += ["", f"## ds/figma-map.json — broken nodeIds {len(broken)}", ""]
        if not fmap:
            lines.append("No map — `figma_sync.py map --figma …`.")
        lines += [f"- `{n}` → `{nid}` missing in Figma" for n, nid in broken]
        lines += ["", "## Variants by story names 🟡 (heuristic, not part of the exit code)", ""]
        lines += [f"- {h}" for h in heur] or ["No findings."]
        lines += ["", "## Catalog ↔ code (for reference)", ""] + [f"- {i}" for i in cinfo] + [""]
    f = out_dir(root) / "drift.md"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mark = "✗" if total else "✓"
    print(f"{mark} {'; '.join(summary)}")
    print(f"  → {f.relative_to(root)}")
    return 1 if total else 0


# ───────────────────────── fetch-rest (CI) ─────────────────────────

REST = "https://api.figma.com/v1"


def rest_get(url, token):
    req = urllib.request.Request(url, headers={"X-Figma-Token": token})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def rest_components(doc):
    """Document from GET /v1/files/:key → components in the EXPORT_DOC format (as export.js: sets + single components)."""
    out = []

    def walk(node, page, parent_type):
        t = node.get("type")
        if t == "COMPONENT_SET" or (t == "COMPONENT" and parent_type != "COMPONENT_SET"):
            vp = {}
            for k, d in (node.get("componentPropertyDefinitions") or {}).items():
                if d.get("type") == "VARIANT":
                    vp[k.split("#")[0]] = d.get("variantOptions") or []
            if t == "COMPONENT_SET" and not vp:  # older files: variants come from child names like "Prop=val, …"
                for ch in node.get("children") or []:
                    for pair in str(ch.get("name", "")).split(","):
                        if "=" in pair:
                            k, v = (x.strip() for x in pair.split("=", 1))
                            vals = vp.setdefault(k, [])
                            if v not in vals:
                                vals.append(v)
            out.append({"name": node.get("name", ""), "nodeId": node.get("id"), "type": t,
                        "variantProps": vp, "page": page})
            if t == "COMPONENT_SET":
                return
        for ch in node.get("children") or []:
            walk(ch, page, t)

    for page in (doc.get("document") or {}).get("children") or []:
        walk(page, page.get("name", ""), "CANVAS")
    return out


def cmd_fetch_rest(args, getter=rest_get):
    root = args.root
    key = args.file_key or (load_map(root) or {}).get("fileKey")
    if not key:
        print("✗ fileKey unknown: pass --file-key or set fileKey in ds/figma-map.json (figma_sync.py map)")
        return 2
    token = os.environ.get("FIGMA_TOKEN")
    if not token:
        print("✗ FIGMA_TOKEN is not set. It is a personal Figma token (Settings → Security → Personal access tokens, "
              "scope file_content:read). Locally keep it in .env, in CI in repository secrets. Never put it in code or chat.")
        return 2
    try:
        doc = getter(f"{REST}/files/{key}", token)
    except urllib.error.HTTPError as e:
        print(f"✗ Figma REST {e.code}: {'token is invalid or has no access to the file' if e.code in (401, 403) else e.reason}")
        return 2
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"✗ Figma REST unavailable: {e}")
        return 2
    comps = rest_components(doc)
    out = Path(args.out) if args.out else out_dir(root) / "rest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fileKey": key, "source": "rest", "variables": [], "components": comps},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"✓ {len(comps)} components from Figma (REST) → {out.relative_to(root) if out.is_relative_to(root) else out}")
    print("  Variables over REST are not available on this plan. Next: check --figma <this file> --only components")
    return 0


# ───────────────────────── init-tokens ─────────────────────────

def token_from_figma(ftype, name, value):
    """Variable value → DTCG token. Units are guessed from the name: duration/delay → ms,
    opacity/weight/line-height/z-index/ratio → number, other FLOATs → px."""
    n = name.lower()
    if ftype == "COLOR":
        c = parse_color(value)
        return c and {"$type": "color", "$value": hex_of(c)}
    if ftype == "FLOAT":
        x = num(float(value))
        if re.search(r"duration|delay", n):
            return {"$type": "duration", "$value": f"{x}ms"}
        if re.search(r"opacity|weight|line-?height|z-?index|ratio|scale", n):
            return {"$type": "number", "$value": x}
        return {"$type": "dimension", "$value": f"{x}px"}
    if ftype == "STRING":
        if re.search(r"font.?family|family", n):
            return {"$type": "fontFamily", "$value": [value]}
        return {"$value": str(value)}
    if ftype == "BOOLEAN":
        return {"$value": bool(value)}
    return None


def cmd_init_tokens(args):
    root = args.root
    dst = path_of(root, "tokens")
    if dst.exists() and not args.force:
        sys.exit(f"✗ {dst.relative_to(root)} already exists, so the source of truth is already in code. "
                 "pull-variables moves values from Figma (diff → --apply); to overwrite everything use --force")
    fig = load_figma(args.figma)
    sem_modes = fig["modes"].get(SEM, [])
    layer = {}
    if sem_modes:
        layer[sem_modes[0]] = "semantic"
        dark = next((m for m in sem_modes[1:] if m.lower() == "dark"), sem_modes[1] if len(sem_modes) > 1 else None)
        if dark:
            layer[dark] = "semantic-dark"
    data, skipped, count = {}, [], 0
    for (col, name), v in sorted(fig["vars"].items()):
        segs = tuple(x.strip() for x in name.split("/"))
        if col == PRIM:
            items = [(segs, next(iter(v["modes"].values())))]
        elif col == SEM:
            items = [((layer[m],) + segs, val) for m, val in v["modes"].items() if m in layer]
        else:
            continue
        for path, val in items:
            if "a" in val:
                tc, tn = val["a"]
                tpath = [x.strip() for x in tn.split("/")]
                tok = {"$value": "{" + ".".join(tpath if tc == PRIM else ["semantic"] + tpath) + "}"}
            else:
                tok = token_from_figma(v["type"], name, val["v"])
            if tok is None:
                skipped.append((f"{col}/{name}", f"type {v['type']} not parsed"))
                continue
            node = data
            for k in path[:-1]:
                node = node.setdefault(k, {})
                if "$value" in node:
                    break
            if "$value" in node or path[-1] in node:
                skipped.append((f"{col}/{name}", "name collides with a group or another token"))
                continue
            node[path[-1]] = tok
            count += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ {dst.relative_to(root)}: {count} tokens from Figma (collections {PRIM}, {SEM})")
    if sem_modes:
        print(f"  semantic modes: " + ", ".join(f"{m} → {layer.get(m, 'skipped')}" for m in sem_modes))
        if [m for m in layer if m not in ("light", "dark")]:
            print("! check compares modes by the names light / dark: rename the modes in Figma or expect \"missing mode\"")
    other = sorted({c for c, _ in fig["vars"] if c not in (PRIM, SEM)})
    if other:
        print("! collections outside the sync (not imported): " + ", ".join(other)
              + "; if these are primitives or semantic under another name, pass --primitives-name / --semantic-name")
    for pth, why in skipped:
        print(f"  · skipped {pth}: {why}")
    print("Composite tokens (typography, shadow) do not live in Variables: move them from TextStyles / EffectStyles by hand.")
    print("Next: check --figma <same file> --only tokens must be green.")
    return 0


# ───────────────────────── pull-variables ─────────────────────────

def from_figma(fval, orig, ttype):
    """Figma value → token value in the form of the original."""
    if ttype == "COLOR":
        c = parse_color(fval)
        if c is None:
            return None
        if isinstance(orig, str) and orig.strip().lower().startswith("rgb"):
            r, g, b, a = c
            return f"rgba({round(r * 255)}, {round(g * 255)}, {round(b * 255)}, {num(a)})"
        h = hex_of(c)
        return h.lower() if isinstance(orig, str) and re.search(r"[a-f]", orig) and not re.search(r"[A-F]", orig) else h
    if ttype == "FLOAT":
        x = float(fval)
        f = to_float(orig)
        unit = f[1] if f else None
        k = {"rem": 1 / 16, "s": 1 / 1000}.get(unit, 1)
        val = num(x * k)
        if isinstance(orig, dict):
            return {**orig, "value": val}
        if isinstance(orig, str):
            return f"{val}{unit or ''}"
        return val
    if ttype == "STRING" and isinstance(orig, list):
        q = '"' if " " in str(fval) else ""
        return [f"{q}{fval}{q}"] + list(orig[1:])
    return fval


def set_path(data, path, value):
    node = data
    for k in path:
        node = node[k]
    node["$value"] = value


def get_tok(data, path):
    node = data
    for k in path:
        node = node[k]
    return node


def cmd_pull(args):
    root = args.root
    data = load_tokens(root)
    model = build_model(data)
    fig = load_figma(args.figma)
    idx = model["index"]
    changes, conflicts, new = [], [], []
    for (col, name), f in fig["vars"].items():
        if col not in (PRIM, SEM):
            continue
        c = idx.get((col, name))
        if c is None:
            new.append(f"{col}/{name} ({f['type']})")
            continue
        for mode, fval in f["modes"].items():
            path = c["paths"].get(mode)
            if path is None:
                new.append(f"{col}/{name} [mode {mode}]")
                continue
            cval = c["values"][mode]
            label = f"{col}/{name} [{mode}]"
            tok = get_tok(data, path)
            if c["fallback"].get(mode):
                same = (cval.get("a") == fval.get("a")) if "a" in fval else (
                    "v" in cval and canon(c["type"], cval["v"]) == canon(c["type"], fval.get("v")))
                if not same:
                    new.append(f"{label}: has its own value in Figma, but there is no token in semantic-{mode}; add it by hand")
                continue
            if "a" in cval:
                if "a" not in fval:
                    conflicts.append(f"{label}: Figma has the value {fval.get('v')} without an alias, which is not allowed in semantic; not imported")
                elif list(fval["a"]) != list(cval["a"]):
                    t = idx.get(tuple(fval["a"]))
                    if t is None:
                        conflicts.append(f"{label}: alias to {'/'.join(fval['a'])}, but there is no such variable in code")
                        continue
                    tpath = next(iter(t["paths"].values()))
                    changes.append((path, tok["$value"], "{" + ".".join(tpath) + "}", label))
                continue
            if "a" in fval:
                conflicts.append(f"{label}: became an alias to {'/'.join(fval['a'])} in Figma; move it by hand")
                continue
            if canon(c["type"], cval["v"]) != canon(c["type"], fval.get("v")):
                nv = from_figma(fval.get("v"), tok["$value"], c["type"])
                if nv is None:
                    conflicts.append(f"{label}: Figma value \"{fval.get('v')}\" not parsed")
                else:
                    changes.append((path, tok["$value"], nv, label))
    print(f"Changed in Figma: {len(changes)} · conflicts: {len(conflicts)} · new in Figma: {len(new)}")
    for path, old, nv, label in changes:
        print(f"  ~ {'.'.join(path)}: {json.dumps(old, ensure_ascii=False)} → {json.dumps(nv, ensure_ascii=False)}")
    for c in conflicts:
        print(f"  ! {c}")
    for n in new:
        print(f"  + {n} (not added)")
    if args.apply and changes:
        for path, _, nv, _ in changes:
            set_path(data, path, nv)
        f = path_of(root, "tokens")
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✓ written to {f.relative_to(root)}. Next: python3 execution/tokens_build.py {root}")
    elif changes:
        print("This is a diff. To write it: --apply")
    return 0


# ───────────────────────── map / storybook-links / code-connect ─────────────────────────

def cmd_map(args):
    root = args.root
    fig = load_figma(args.figma)
    code = code_components(root)
    figc, dups, _ = figma_components(fig, code)
    stories = story_info(root, code)
    f = path_of(root, "figma_map")
    fmap = load_map(root) or {}
    comps = fmap.setdefault("components", {})
    node_ids = {c["nodeId"] for c in fig["components"]}
    key = args.file_key or fmap.get("fileKey") or fig.get("fileKey")
    if key:
        fmap["fileKey"] = key
    added, fixed, kept = [], [], []
    for n in sorted(code):
        if n not in figc:
            continue
        fc = figc[n]
        e = comps.setdefault(n, {})
        if e.get("manual"):
            kept.append(n)
            continue
        if not e:
            added.append(n)
        auto = {"nodeId": fc["nodeId"], "figmaName": fc["name"], "source": code[n]}
        if stories.get(n, {}).get("title"):
            auto["storyTitle"] = stories[n]["title"]
        for k, v in auto.items():
            if k not in e or args.refresh:
                e[k] = v
            elif k == "nodeId" and e[k] != v and e[k] not in node_ids:
                fixed.append(f"{n}: {e[k]} → {v}")
                e[k] = v
            elif e[k] != v and n not in kept:
                kept.append(n)
    fmap["components"] = dict(sorted(comps.items()))
    order = ["fileKey", "collections", "components"]
    fmap = {**{k: fmap[k] for k in order if k in fmap}, **{k: v for k, v in fmap.items() if k not in order}}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(fmap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ {f.relative_to(root)}: {len(comps)} in the map, {len(added)} new, {len(fixed)} nodeIds fixed, "
          f"{len(kept)} manual edits kept")
    for x in fixed:
        print(f"  ~ {x}")
    no_fig = sorted(set(code) - set(figc))
    no_code = sorted(set(figc) - set(code))
    if no_fig:
        print(f"  missing in Figma ({len(no_fig)}): " + ", ".join(no_fig))
    if no_code:
        print(f"  missing in code ({len(no_code)}): " + ", ".join(no_code))
    if not key:
        print("! fileKey unknown: pass --file-key (from the URL figma.com/design/<fileKey>/…)")
    return 0


def figma_url(key, node_id):
    return f"https://www.figma.com/design/{key}/?node-id={node_id.replace(':', '-')}"


LINKS_HEADER = ("/* Generated by execution/figma_sync.py storybook-links from ds/figma-map.json; do not edit by hand.\n"
                "   The key is the component title in Storybook or its last segment (name = anchor). */\n")


def cmd_links(args):
    root = args.root
    fmap = load_map(root)
    if fmap is None:
        print("· ds/figma-map.json not found, so no Figma links are generated (normal until the design system is in Figma)")
        return 0
    key = fmap.get("fileKey")
    links = {}
    if not key:
        print("! ds/figma-map.json has no fileKey, so figma-links.ts will be empty")
    else:
        for n, e in sorted((fmap.get("components") or {}).items()):
            if not e.get("nodeId"):
                continue
            url = figma_url(key, e["nodeId"])
            links[n] = url
            if e.get("storyTitle") and e["storyTitle"] != n:
                links[e["storyTitle"]] = url
    body = LINKS_HEADER + "export const figmaLinks: Record<string, string> = {\n"
    body += "".join(f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v)},\n" for k, v in sorted(links.items()))
    body += "};\n"
    f = path_of(root, "figma_links")
    if not f.parent.parent.exists():
        print(f"! {f.parent.parent.relative_to(root)} not found, skipping")
        return 0
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists() or f.read_text(encoding="utf-8") != body:
        f.write_text(body, encoding="utf-8")
    print(f"✓ {f.relative_to(root)}: {len(links)} links")
    return 0


def cmd_code_connect(args):
    root = args.root
    fmap = load_map(root)
    if fmap is None:
        sys.exit("✗ ds/figma-map.json not found: run figma_sync.py map --figma … first")
    out = []
    for n, e in sorted((fmap.get("components") or {}).items()):
        if not e.get("nodeId") or not e.get("source"):
            continue
        src = e["source"]
        if args.label == "Storybook":
            s = Path(src).with_name(f"{n}.stories.tsx")
            src = str(s) if (root / s).exists() else src
        out.append({"nodeId": e["nodeId"], "componentName": n, "source": src, "label": args.label})
    f = out_dir(root) / "code-connect.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ {f.relative_to(root)}: {len(out)} mappings (label {args.label}), fileKey {fmap.get('fileKey') or '—'}")
    print("  send_code_connect_mappings works only on Organization / Enterprise plans.")
    return 0


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=lambda p: Path(p).resolve(), default=TEMPLATE_ROOT,
                        help="project root (default: the template root)")
    common.add_argument("--primitives-name", help="name of the primitives collection in Figma (default: primitives "
                        "or ds/figma-map.json → collections.primitives)")
    common.add_argument("--semantic-name", help="name of the semantic collection in Figma (default: semantic "
                        "or ds/figma-map.json → collections.semantic)")
    ap = argparse.ArgumentParser(description="Design system sync: code ↔ Figma ↔ Storybook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push-variables-js", parents=[common], help="JS upsert of Variables for use_figma")
    p.add_argument("--limit", type=int, default=PART_LIMIT, help="max bytes per file")
    p.add_argument("--show-primitive-colors", action="store_true", help="do not hide color primitives from pickers")
    p.set_defaults(fn=cmd_push)
    p = sub.add_parser("export-js", parents=[common], help="read-only JS that exports Variables and components")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("check", parents=[common], help="code ↔ Figma drift check → drift.md")
    p.add_argument("--figma", required=True)
    p.add_argument("--only", choices=["tokens", "components"])
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("init-tokens", parents=[common], help="once: Figma Variables → tokens.json")
    p.add_argument("--figma", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init_tokens)
    p = sub.add_parser("pull-variables", parents=[common], help="values from Figma → tokens.json")
    p.add_argument("--figma", required=True)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_pull)
    p = sub.add_parser("map", parents=[common], help="ds/figma-map.json by name")
    p.add_argument("--figma", required=True)
    p.add_argument("--file-key")
    p.add_argument("--refresh", action="store_true", help="overwrite auto fields (except manual: true)")
    p.set_defaults(fn=cmd_map)
    p = sub.add_parser("storybook-links", parents=[common], help="app/src/stories/figma-links.ts")
    p.set_defaults(fn=cmd_links)
    p = sub.add_parser("code-connect", parents=[common], help="payload send_code_connect_mappings")
    p.add_argument("--label", choices=["React", "Storybook"], default="React")
    p.set_defaults(fn=cmd_code_connect)
    p = sub.add_parser("fetch-rest", parents=[common], help="Figma components via REST (FIGMA_TOKEN) → rest.json")
    p.add_argument("--file-key")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_fetch_rest)
    args = ap.parse_args(argv)
    global PRIM, SEM
    names = ((load_map(args.root) or {}).get("collections") or {})
    PRIM = args.primitives_name or names.get("primitives") or "primitives"
    SEM = args.semantic_name or names.get("semantic") or "semantic"
    if args.cmd in ("check", "pull-variables", "map", "init-tokens"):
        args.figma = str(Path(args.figma).resolve())
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

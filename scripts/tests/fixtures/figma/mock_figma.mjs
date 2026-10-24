// Figma Plugin API mock for the figma_sync.py tests (no network, no Figma).
// node mock_figma.mjs [--components comps.json] push-1.js [push-2.js ...] -- export.js
// Runs the push files twice (the second run checks idempotency), then export.js.
// Prints JSON: {first: [...results], second: [...], export: {...}}.
import { readFileSync } from "node:fs";

const args = process.argv.slice(2);
let comps = [];
const ci = args.indexOf("--components");
if (ci >= 0) { comps = JSON.parse(readFileSync(args[ci + 1], "utf8")); args.splice(ci, 2); }
const sep = args.indexOf("--");
const pushFiles = sep >= 0 ? args.slice(0, sep) : args;
const exportFile = sep >= 0 ? args[sep + 1] : null;

let seq = 0;
const cols = [];
const vars = [];
const SCOPES = new Set(["ALL_SCOPES", "TEXT_CONTENT", "CORNER_RADIUS", "WIDTH_HEIGHT", "GAP", "ALL_FILLS", "FRAME_FILL",
  "SHAPE_FILL", "TEXT_FILL", "STROKE_COLOR", "STROKE_FLOAT", "EFFECT_FLOAT", "EFFECT_COLOR", "OPACITY", "FONT_FAMILY",
  "FONT_STYLE", "FONT_WEIGHT", "FONT_SIZE", "LINE_HEIGHT", "LETTER_SPACING", "PARAGRAPH_SPACING", "PARAGRAPH_INDENT"]);

function makeVar(name, col, type) {
  const v = {
    id: "VariableID:" + ++seq, name, variableCollectionId: col.id, resolvedType: type, valuesByMode: {},
    _scopes: ["ALL_SCOPES"], description: "", codeSyntax: {},
    get scopes() { return this._scopes; },
    set scopes(s) { for (const x of s) if (!SCOPES.has(x)) throw new Error("bad scope " + x); this._scopes = [...s]; },
    setValueForMode(m, val) {
      if (!col.modes.some(x => x.modeId === m)) throw new Error("bad mode " + m);
      if (val && val.type === "VARIABLE_ALIAS") {
        const t = vars.find(x => x.id === val.id);
        if (!t) throw new Error("alias target missing");
        if (t.resolvedType !== type) throw new Error("alias type mismatch");
      } else if (type === "COLOR" && (typeof val !== "object" || !("r" in val))) throw new Error("COLOR expects RGBA");
      else if (type === "FLOAT" && typeof val !== "number") throw new Error("FLOAT expects number");
      else if (type === "STRING" && typeof val !== "string") throw new Error("STRING expects string");
      this.valuesByMode[m] = val;
    },
    setVariableCodeSyntax(p, s) { this.codeSyntax[p] = s; },
  };
  return v;
}

const pages = [];
for (const c of comps) {
  let page = pages.find(p => p.name === c.page);
  if (!page) { page = { name: c.page, nodes: [] }; pages.push(page); }
  const defs = {};
  for (const [k, vals] of Object.entries(c.variantProps || {})) defs[k] = { type: "VARIANT", variantOptions: vals };
  const node = { id: c.nodeId, name: c.name, type: c.type, componentPropertyDefinitions: defs, parent: null, children: [] };
  page.nodes.push(node);
  if (c.type === "COMPONENT_SET") page.nodes.push({ id: c.nodeId + "-v", name: "x=y", type: "COMPONENT", parent: node });
}

globalThis.figma = {
  fileKey: "MOCKKEY",
  variables: {
    getLocalVariableCollectionsAsync: async () => [...cols],
    getLocalVariablesAsync: async () => [...vars],
    getVariableByIdAsync: async id => vars.find(v => v.id === id) || null,
    createVariableCollection(name) {
      const c = {
        id: "VariableCollectionId:" + ++seq, name, modes: [{ modeId: "m" + ++seq, name: "Mode 1" }], get variableIds() {
          return vars.filter(v => v.variableCollectionId === this.id).map(v => v.id);
        },
        renameMode(id, n) { this.modes.find(m => m.modeId === id).name = n; },
        addMode(n) { const id = "m" + ++seq; this.modes.push({ modeId: id, name: n }); return id; },
      };
      cols.push(c);
      return c;
    },
    createVariable(name, col, type) {
      if (typeof col === "string") throw new Error("pass collection node, not id");
      if (vars.some(v => v.variableCollectionId === col.id && v.name === name)) throw new Error("duplicate " + name);
      const v = makeVar(name, col, type);
      vars.push(v);
      return v;
    },
    createVariableAlias(v) { return { type: "VARIABLE_ALIAS", id: v.id }; },
  },
  loadAllPagesAsync: async () => {},
  root: {
    get children() {
      return pages.map(p => ({
        name: p.name,
        loadAsync: async () => {},
        findAllWithCriteria: ({ types }) => p.nodes.filter(n => types.includes(n.type)),
      }));
    },
  },
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = async f => new AsyncFunction(readFileSync(f, "utf8"))();
const out = { first: [], second: [], export: null };
for (const f of pushFiles) out.first.push(await run(f));
for (const f of pushFiles) out.second.push(await run(f));
if (exportFile) out.export = await run(exportFile);
process.stdout.write(JSON.stringify(out));

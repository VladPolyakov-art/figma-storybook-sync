#!/usr/bin/env python3
"""
Tests for execution/figma_sync.py — no network and no Figma.

Fixtures: execution/tests/fixtures/figma/
  project/     — a mini project (tokens.json with semantic and semantic-dark, ds/components.md, Button and Card in app/src/components/ds)
  match.json   — an export.js response that matches the code
  drift.json   — a response with drift, in an MCP wrapper ({content: [{type: text, text: "..."}]})
  empty.json   — an empty Figma file
  mock_figma.mjs — a Plugin API mock: runs push-variables*.js (twice) and export.js in node

Run: python3 -m unittest execution/tests/test_figma_sync.py -v  (from the template root)
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import figma_sync as fs  # noqa: E402

FX = HERE / "fixtures" / "figma"
NODE = shutil.which("node")


def run(*argv):
    """Run a subcommand → (exit code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            code = fs.main([str(a) for a in argv])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="figma_sync_"))
        self.root = self.tmp / "p"
        shutil.copytree(FX / "project", self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestModel(unittest.TestCase):
    def setUp(self):
        self.model = fs.build_model(json.loads((FX / "project" / "tokens" / "tokens.json").read_text("utf-8")))
        self.idx = self.model["index"]

    def test_collections_and_modes(self):
        self.assertEqual(self.model["modes"], {"primitives": ["default"], "semantic": ["light", "dark"]})
        self.assertIn(("primitives", "color/gray/900"), self.idx)
        self.assertIn(("semantic", "surface/default"), self.idx)

    def test_types_and_units(self):
        self.assertEqual(self.idx[("primitives", "space/8")]["values"]["default"], {"v": 8})  # 0.5rem × 16
        self.assertEqual(self.idx[("primitives", "space/4")]["values"]["default"], {"v": 4})
        self.assertEqual(self.idx[("primitives", "duration/fast")]["type"], "FLOAT")
        fam = self.idx[("primitives", "font/family/sans")]
        self.assertEqual((fam["type"], fam["values"]["default"]["v"]), ("STRING", "Geist"))
        scrim = self.idx[("primitives", "color/scrim")]["values"]["default"]["v"]
        self.assertAlmostEqual(scrim["a"], 0.4, places=3)
        self.assertEqual([p for p, _ in self.model["skipped"]], ["typography.body"])

    def test_alias_resolution(self):
        sd = self.idx[("semantic", "surface/default")]
        self.assertEqual(sd["values"]["light"], {"a": ["primitives", "color/gray/00"]})
        self.assertEqual(sd["values"]["dark"], {"a": ["primitives", "color/gray/900"]})
        self.assertEqual(sd["type"], "COLOR")
        bf = self.idx[("semantic", "border/focus")]
        self.assertTrue(bf["fallback"].get("dark"))  # not in semantic-dark → the light value
        cv = fs.code_vars(self.model)
        self.assertEqual(fs.resolve(cv, self.model["modes"], "semantic", "surface/default", "dark"), "#111827FF")
        self.assertEqual(fs.resolve(cv, self.model["modes"], "semantic", "space/inline", "light"), 8)

    def test_scopes(self):
        self.assertEqual(self.idx[("semantic", "text/primary")]["scopes"], ["TEXT_FILL"])
        self.assertEqual(self.idx[("semantic", "surface/default")]["scopes"], ["FRAME_FILL", "SHAPE_FILL"])
        self.assertEqual(self.idx[("semantic", "border/focus")]["scopes"], ["STROKE_COLOR"])
        self.assertEqual(self.idx[("primitives", "color/gray/900")]["scopes"], [])
        self.assertEqual(self.idx[("primitives", "radius/md")]["scopes"], ["CORNER_RADIUS"])
        self.assertEqual(self.idx[("primitives", "space/4")]["scopes"], ["GAP"])
        self.assertEqual(self.idx[("primitives", "font/weight/semibold")]["scopes"], ["FONT_WEIGHT"])
        self.assertEqual(self.idx[("primitives", "font/family/sans")]["scopes"], ["FONT_FAMILY"])
        self.assertEqual(fs.scopes_for("semantic", "typography/body/line-height", "FLOAT"), ["LINE_HEIGHT"])
        self.assertEqual(fs.scopes_for("semantic", "border-width/default", "FLOAT"), ["STROKE_FLOAT"])

    def test_parse_color(self):
        self.assertEqual(fs.hex_of(fs.parse_color("#abc")), "#AABBCC")
        self.assertEqual(fs.hex_of(fs.parse_color("rgba(17, 24, 39, 0.4)"), True), "#11182766")
        self.assertIsNone(fs.parse_color("oklch(0.5 0.1 200)"))


class TestJs(Base):
    def test_push_and_export_files(self):
        code, out = run("push-variables-js", "--root", self.root)
        self.assertEqual(code, 0, out)
        f = self.root / ".tmp" / "figma" / "push-variables.js"
        self.assertTrue(f.exists())
        js = f.read_text("utf-8")
        self.assertIn("createVariableAlias", js)
        self.assertNotIn("throw", js)
        self.assertTrue(js.rstrip().endswith("return R;"))
        code, _ = run("export-js", "--root", self.root)
        ex = (self.root / ".tmp" / "figma" / "export.js").read_text("utf-8")
        self.assertIn("loadAllPagesAsync", ex)
        self.assertIn("findAllWithCriteria", ex)
        self.assertNotIn("setCurrentPageAsync", ex)
        for bad in ("setValueForMode", "createVariable(", ".remove()"):
            self.assertNotIn(bad, ex)

    def test_split_into_parts(self):
        code, out = run("push-variables-js", "--root", self.root, "--limit", "5000")
        self.assertEqual(code, 0, out)
        parts = sorted((self.root / ".tmp" / "figma").glob("push-variables-*.js"))
        self.assertGreaterEqual(len(parts), 2)
        self.assertFalse((self.root / ".tmp" / "figma" / "push-variables.js").exists())
        first = parts[0].read_text("utf-8")
        self.assertIn('"name":"primitives"', first)
        self.assertNotIn('"name":"semantic"', first)  # primitives first
        for p in parts:
            self.assertLessEqual(len(p.read_bytes()), 5000 + 600)

    @unittest.skipUnless(NODE, "node not found")
    def test_node_syntax(self):
        run("push-variables-js", "--root", self.root, "--limit", "5000")
        run("export-js", "--root", self.root)
        for f in (self.root / ".tmp" / "figma").glob("*.js"):
            wrapped = self.tmp / (f.stem + ".check.mjs")
            wrapped.write_text("export default (async () => {\n" + f.read_text("utf-8") + "\n})();\n", "utf-8")
            r = subprocess.run([NODE, "--check", str(wrapped)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{f.name}: {r.stderr}")

    @unittest.skipUnless(NODE, "node not found")
    def test_mock_roundtrip_idempotent(self):
        """push to the Figma mock → a repeated push changes nothing → export → check with no drift."""
        run("push-variables-js", "--root", self.root, "--limit", "5000")
        run("export-js", "--root", self.root)
        d = self.root / ".tmp" / "figma"
        comps = self.tmp / "comps.json"
        comps.write_text(json.dumps(json.loads((FX / "match.json").read_text("utf-8"))["components"]), "utf-8")
        parts = sorted(d.glob("push-variables-*.js"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
        r = subprocess.run([NODE, str(FX / "mock_figma.mjs"), "--components", str(comps), *map(str, parts),
                            "--", str(d / "export.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout)
        self.assertEqual([e for x in res["first"] for e in x["errors"]], [])
        self.assertEqual(sum(x["created"] for x in res["first"]), 14)
        self.assertEqual(sum(x["created"] + x["updated"] for x in res["second"]), 0)
        self.assertEqual(sum(x["skipped"] for x in res["second"]), 14)
        exp = d / "export.json"
        exp.write_text(json.dumps(res["export"]), "utf-8")
        self.assertEqual(res["export"]["fileKey"], "MOCKKEY")
        names = {c["name"] for c in res["export"]["components"]}
        self.assertEqual(names, {"Components/Button", "Card", "Icon/Check"})  # variants inside a set are not counted
        code, out = run("check", "--root", self.root, "--figma", exp)
        self.assertEqual(code, 0, out + (d / "drift.md").read_text("utf-8"))


class TestCheck(Base):
    def test_match(self):
        code, out = run("check", "--root", self.root, "--figma", FX / "match.json")
        self.assertEqual(code, 0, out)
        md = (self.root / ".tmp" / "figma" / "drift.md").read_text("utf-8")
        self.assertIn("Tokens — drift 0", md)
        self.assertIn("`Tooltip`: in ds/components.md, missing in code", md)

    def test_drift(self):
        code, out = run("check", "--root", self.root, "--figma", FX / "drift.json")
        self.assertEqual(code, 1)
        md = (self.root / ".tmp" / "figma" / "drift.md").read_text("utf-8")
        for needle in ("| value | `primitives/color/gray/900 [default]` | #111827FF | #0F172AFF |",
                       "| missing in Figma | `primitives/radius/md` |",
                       "| missing in code | `primitives/color/new/500` |",
                       "| alias | `semantic/text/primary [light]` | → primitives/color/gray/900 | → primitives/color/accent/600 |",
                       "| alias unbound in Figma | `semantic/space/inline [light]` |",
                       "| missing in Figma | `Card` |", "| only in Figma | `Badge` |",
                       "variant: only Figma ['tertiary'] · only code (variant) ['ghost']"):
            self.assertIn(needle, md)
        # same alias: the target value is not duplicated as a semantic row
        self.assertNotIn("semantic/surface/default [dark]", md)

    def test_empty_figma(self):
        code, out = run("check", "--root", self.root, "--figma", FX / "empty.json")
        self.assertEqual(code, 1)
        self.assertIn("tokens: 14 in code, 0 in Figma, drift 14", out)

    def test_broken_map(self):
        (self.root / "ds" / "figma-map.json").write_text(json.dumps(
            {"fileKey": "K", "components": {"Button": {"nodeId": "1:2"}, "Card": {"nodeId": "9:99"}}}), "utf-8")
        code, out = run("check", "--root", self.root, "--figma", FX / "match.json", "--only", "components")
        self.assertEqual(code, 1)
        self.assertIn("broken nodeIds in the map 1", out)
        self.assertIn("`Card` → `9:99` missing in Figma", (self.root / ".tmp" / "figma" / "drift.md").read_text("utf-8"))

    def test_story_name_heuristic(self):
        fig = json.loads((FX / "match.json").read_text("utf-8"))
        fig["components"][1]["type"] = "COMPONENT_SET"
        fig["components"][1]["variantProps"] = {"Elevation": ["Flat", "Elevated"]}
        p = self.tmp / "heur.json"
        p.write_text(json.dumps(fig), "utf-8")
        code, _ = run("check", "--root", self.root, "--figma", p, "--only", "components")
        self.assertEqual(code, 0)  # the heuristic is not part of the exit code
        md = (self.root / ".tmp" / "figma" / "drift.md").read_text("utf-8")
        self.assertIn("`Card` · Elevation: no story with Flat", md)


class TestPull(Base):
    def test_diff_then_apply(self):
        tok = self.root / "tokens" / "tokens.json"
        before = tok.read_text("utf-8")
        code, out = run("pull-variables", "--root", self.root, "--figma", FX / "drift.json")
        self.assertEqual(code, 0)
        self.assertIn('~ color.gray.900: "#111827" → "#0F172A"', out)
        self.assertIn('~ space.4: {"value": 4, "unit": "px"} → {"value": 6, "unit": "px"}', out)
        self.assertIn('~ semantic.text.primary: "{color.gray.900}" → "{color.accent.600}"', out)
        self.assertIn("without an alias, which is not allowed in semantic", out)
        self.assertIn("+ primitives/color/new/500", out)
        self.assertEqual(tok.read_text("utf-8"), before)  # does not write without --apply
        run("pull-variables", "--root", self.root, "--figma", FX / "drift.json", "--apply")
        data = json.loads(tok.read_text("utf-8"))
        self.assertEqual(data["color"]["gray"]["900"]["$value"], "#0F172A")
        self.assertEqual(data["space"]["4"]["$value"], {"value": 6, "unit": "px"})
        self.assertEqual(data["semantic"]["text"]["primary"]["$value"], "{color.accent.600}")
        self.assertEqual(data["semantic"]["space"]["inline"]["$value"], "{space.8}")  # the unbound one is untouched
        self.assertNotIn("new", data["color"])

    def test_figma_component_names(self):
        fig = {"components": [
            {"name": "Icon/x", "nodeId": "1", "type": "COMPONENT"},
            {"name": "IconButton", "nodeId": "2", "type": "COMPONENT_SET"},
            {"name": "DataTable/Row", "nodeId": "3", "type": "COMPONENT_SET"},
            {"name": "Forms/Select", "nodeId": "4", "type": "COMPONENT_SET"}]}
        out, _, ignored = fs.figma_components(fig, {"IconButton": "", "DataTable": "", "Select": ""})
        self.assertEqual(ignored, ["Icon/x"])  # icons are ignored, IconButton is not
        self.assertEqual(out["DataTable"]["nodeId"], "3")  # part → parent
        self.assertEqual(out["Select"]["nodeId"], "4")  # Category/Name
        self.assertNotIn("Row", out)

    def test_load_figma_dir_merges_parts(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "a.json").write_text(json.dumps({"fileKey": "K", "variables": [
            {"collection": "P", "mode": "m", "name": "x", "type": "FLOAT", "value": 1}]}), "utf-8")
        (d / "b.json").write_text(json.dumps({"components": [{"name": "Button", "nodeId": "1:1", "type": "COMPONENT_SET"}]}), "utf-8")
        fig = fs.load_figma(d)
        self.assertEqual((fig["fileKey"], len(fig["vars"]), len(fig["components"])), ("K", 1, 1))

    def test_value_forms(self):
        self.assertEqual(fs.from_figma(24, "1rem", "FLOAT"), "1.5rem")
        self.assertEqual(fs.to_figma({"value": -0.01, "unit": "em"}, "dimension"), ("FLOAT", -0.01))  # letter-spacing
        self.assertEqual(fs.from_figma(0.04, {"value": -0.01, "unit": "em"}, "FLOAT"), {"value": 0.04, "unit": "em"})
        self.assertEqual(fs.from_figma("#11182780", "rgba(17, 24, 39, 0.4)", "COLOR"), "rgba(17, 24, 39, 0.502)")
        self.assertEqual(fs.from_figma("Inter", ["Geist", "sans-serif"], "STRING"), ["Inter", "sans-serif"])


class TestMapLinksCodeConnect(Base):
    def test_map_preserves_manual(self):
        fmap = self.root / "ds" / "figma-map.json"
        fmap.write_text(json.dumps({"components": {
            "Button": {"nodeId": "1:2", "figmaName": "Button (manual name)", "note": "mine"},
            "Card": {"nodeId": "7:7", "manual": True}}}), "utf-8")
        code, out = run("map", "--root", self.root, "--figma", FX / "match.json")
        self.assertEqual(code, 0, out)
        m = json.loads(fmap.read_text("utf-8"))
        self.assertEqual(m["fileKey"], "FIXTUREKEY")
        b = m["components"]["Button"]
        self.assertEqual(b["figmaName"], "Button (manual name)")
        self.assertEqual(b["note"], "mine")
        self.assertEqual(b["source"], "app/src/components/ds/Button/Button.tsx")
        self.assertEqual(b["storyTitle"], "Components/Button")
        self.assertEqual(m["components"]["Card"], {"nodeId": "7:7", "manual": True})

    def test_map_fixes_broken_node(self):
        fmap = self.root / "ds" / "figma-map.json"
        fmap.write_text(json.dumps({"components": {"Card": {"nodeId": "404:1"}}}), "utf-8")
        run("map", "--root", self.root, "--figma", FX / "match.json", "--file-key", "OVERRIDE")
        m = json.loads(fmap.read_text("utf-8"))
        self.assertEqual(m["components"]["Card"]["nodeId"], "1:5")
        self.assertEqual(m["fileKey"], "OVERRIDE")
        self.assertNotIn("Icon/Check", json.dumps(m))

    def test_storybook_links(self):
        code, out = run("storybook-links", "--root", self.root)
        self.assertEqual(code, 0)
        self.assertIn("not found", out)  # without a map it exits quietly
        run("map", "--root", self.root, "--figma", FX / "match.json")
        run("storybook-links", "--root", self.root)
        ts = (self.root / "app" / "src" / "stories" / "figma-links.ts").read_text("utf-8")
        self.assertIn("do not edit by hand", ts)
        self.assertIn("export const figmaLinks: Record<string, string> = {", ts)
        self.assertIn('"Components/Button": "https://www.figma.com/design/FIXTUREKEY/?node-id=1-2"', ts)
        self.assertIn('"Card": "https://www.figma.com/design/FIXTUREKEY/?node-id=1-5"', ts)

    def test_code_connect(self):
        run("map", "--root", self.root, "--figma", FX / "match.json")
        code, _ = run("code-connect", "--root", self.root)
        self.assertEqual(code, 0)
        cc = json.loads((self.root / ".tmp" / "figma" / "code-connect.json").read_text("utf-8"))
        self.assertEqual(cc[0], {"nodeId": "1:2", "componentName": "Button",
                                 "source": "app/src/components/ds/Button/Button.tsx", "label": "React"})
        run("code-connect", "--root", self.root, "--label", "Storybook")
        cc = json.loads((self.root / ".tmp" / "figma" / "code-connect.json").read_text("utf-8"))
        self.assertTrue(cc[0]["source"].endswith("Button.stories.tsx"))
        self.assertEqual(cc[0]["label"], "Storybook")

    def test_custom_collection_names(self):
        fig = json.loads((FX / "match.json").read_text("utf-8"))
        ren = {"primitives": "Primitive", "semantic": "Semantic"}
        for v in fig["variables"]:
            v["collection"] = ren[v["collection"]]
            if "aliasOf" in v:
                v["aliasOf"]["collection"] = ren[v["aliasOf"]["collection"]]
        p = self.tmp / "renamed.json"
        p.write_text(json.dumps(fig), "utf-8")
        code, _ = run("check", "--root", self.root, "--figma", p, "--only", "tokens")
        self.assertEqual(code, 1)
        code, out = run("check", "--root", self.root, "--figma", p, "--only", "tokens",
                        "--primitives-name", "Primitive", "--semantic-name", "Semantic")
        self.assertEqual(code, 0, out)


class TestCustomPaths(Base):
    def test_figma_sync_json_paths(self):
        """A different layout (src/ instead of app/src/, design-tokens/) via figma-sync.json → paths."""
        shutil.move(str(self.root / "app" / "src"), str(self.root / "src"))
        shutil.move(str(self.root / "tokens"), str(self.root / "design-tokens"))
        (self.root / "figma-sync.json").write_text(json.dumps({"paths": {
            "tokens": "design-tokens/tokens.json", "components_dir": "src/components",
            "figma_links": "src/stories/figma-links.ts", "figma_map": "figma-map.json", "out": ".cache/figma"}}), "utf-8")
        code, out = run("check", "--root", self.root, "--figma", FX / "match.json")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.root / ".cache" / "figma" / "drift.md").exists())
        run("map", "--root", self.root, "--figma", FX / "match.json")
        m = json.loads((self.root / "figma-map.json").read_text("utf-8"))
        self.assertEqual(m["components"]["Button"]["source"], "src/components/ds/Button/Button.tsx")
        run("storybook-links", "--root", self.root)
        self.assertTrue((self.root / "src" / "stories" / "figma-links.ts").exists())


class TestInitTokens(Base):
    def test_roundtrip_from_figma(self):
        """Figma-first project: init-tokens from the export, then a token check with no drift."""
        (self.root / "tokens" / "tokens.json").unlink()
        code, out = run("init-tokens", "--root", self.root, "--figma", FX / "match.json")
        self.assertEqual(code, 0, out)
        code, out = run("check", "--root", self.root, "--figma", FX / "match.json", "--only", "tokens")
        self.assertEqual(code, 0, out)

    def test_refuses_to_overwrite(self):
        code, out = run("init-tokens", "--root", self.root, "--figma", FX / "match.json")
        self.assertNotEqual(code, 0)


class TestFetchRest(unittest.TestCase):
    """fetch-rest: parsing GET /v1/files/:key without a network (the getter is replaced)."""
    DOC = {"document": {"children": [{"type": "CANVAS", "name": "UI Kit", "children": [
        {"type": "FRAME", "name": "Buttons", "children": [
            {"type": "COMPONENT_SET", "id": "1:1", "name": "Button", "componentPropertyDefinitions": {
                "variant": {"type": "VARIANT", "variantOptions": ["primary", "secondary"]},
                "label#12:0": {"type": "TEXT"}},
             "children": [{"type": "COMPONENT", "id": "1:2", "name": "variant=primary"}]},
            {"type": "COMPONENT_SET", "id": "2:1", "name": "Tag", "children": [
                {"type": "COMPONENT", "id": "2:2", "name": "Tone=Neutral, Size=sm"},
                {"type": "COMPONENT", "id": "2:3", "name": "Tone=Danger, Size=sm"}]}]},
        {"type": "COMPONENT", "id": "3:1", "name": "Logo"}]}]}}

    def test_components_like_plugin_export(self):
        comps = {c["name"]: c for c in fs.rest_components(self.DOC)}
        self.assertEqual(sorted(comps), ["Button", "Logo", "Tag"])  # variants inside sets are not counted
        self.assertEqual(comps["Button"]["variantProps"], {"variant": ["primary", "secondary"]})
        self.assertEqual(comps["Tag"]["variantProps"], {"Tone": ["Neutral", "Danger"], "Size": ["sm"]})
        self.assertEqual((comps["Logo"]["type"], comps["Logo"]["page"]), ("COMPONENT", "UI Kit"))

    def test_cmd_writes_rest_json_without_token_leak(self):
        import os
        root = Path(tempfile.mkdtemp())
        args = type("A", (), {"root": root, "file_key": "KEY", "out": None})()
        seen = {}
        def getter(url, token):
            seen["url"], seen["token"] = url, token
            return self.DOC
        os.environ["FIGMA_TOKEN"] = "test-token"
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                code = fs.cmd_fetch_rest(args, getter=getter)
        finally:
            del os.environ["FIGMA_TOKEN"]
        self.assertEqual(code, 0)
        self.assertTrue(seen["url"].endswith("/files/KEY"))
        data = json.loads((root / ".tmp" / "figma" / "rest.json").read_text("utf-8"))
        self.assertEqual((data["fileKey"], data["variables"], len(data["components"])), ("KEY", [], 3))
        self.assertNotIn("test-token", buf.getvalue() + json.dumps(data))

    def test_no_token_exit_2(self):
        import os
        os.environ.pop("FIGMA_TOKEN", None)
        args = type("A", (), {"root": Path(tempfile.mkdtemp()), "file_key": "KEY", "out": None})()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fs.cmd_fetch_rest(args, getter=lambda u, t: self.DOC), 2)


if __name__ == "__main__":
    unittest.main()

# Figma MCP and Plugin API pitfalls

Collected from a real sync (a real project: 322 Variables, 54 components). Read before the first `use_figma` in a project.

## Plans and access
| What | Where it works |
|---|---|
| Figma MCP (`use_figma`, `get_*`) | Professional and above, Dev or Full seat. Starter has hard call limits, and a design system build hits them quickly |
| Code Connect (`send_code_connect_mappings`) | Organization / Enterprise only |
| REST API: files, components, variants (`fetch-rest`) | any plan, personal token with scope `file_content:read` |
| REST API: Variables | Enterprise only. That is why in CI tokens are checked only against `tokens.json`, and the full Variables check is done by `check` through MCP |
| Storybook Connect (Figma plugin) | through Chromatic, the free plan is enough |

On an access or limit error → `whoami`, check which team the file is in. Do not work around it: ask the user to connect MCP or move the file to a Pro team.

## `use_figma` calls
- **The response must be at most 48 KB.** The export of a large design system does not fit: `export-js` writes parts (`export-variables-<collection>.js`, `export-components.js`). Put the responses in `.tmp/figma/export/*.json`; `--figma` accepts a folder and merges them.
- **Keep the request under ~40 KB.** `push-variables-js` splits itself into parts; send them one by one, in order (primitives first, then semantic).
- **`figma.loadAllPagesAsync()` is forbidden in MCP.** Load pages one at a time: `page.loadAsync()`.
- **No `throw` at the end of a mutating call**: it rolls back the whole transaction. Return the report with `return`.
- **The whole script goes in every call**: Figma reads nothing from disk.
- **Verify by the actual result, not the log.** After a write, make a read-only call (`boundVariables`, `textStyleId`, node types) and `get_screenshot`.
- One logical object per call: one component with a variant matrix of ≤ 24 cells, or one screen.

## Variables and styles
- The file already exists with its own collection names (`Primitive` / `Semantic`) → write them to the map once: `"collections": {"primitives": "Primitive", "semantic": "Semantic"}`. After that push and check work without flags.
- Letter-spacing in `em` is FLOAT only (Figma does not bind letter spacing to STRING).
- Composite tokens (typography, shadow objects) do not fit into Variables: they are TextStyles and EffectStyles. The script lists them in the report as skipped.
- Normalize style names: `name.replace(/\s*\/\s*/g, '/')`.

## Components
- Fonts: `loadFontAsync` before writing `characters`.
- `layoutSizingHorizontal/Vertical` is set only after `appendChild` into an auto-layout parent.
- `resize()` on an auto-layout component sets FIXED on the primary axis. For buttons and chips, restore `primaryAxisSizingMode = "AUTO"`.
- Do not read `variantProperties` while renaming variants: the set is temporarily "with errors". Parse the variant name.
- Check variant values against code before building (`State=error`, not `invalid`), otherwise `check` will report drift.
- Icons: `createNodeFromSvg` with the target `width/height` in the SVG itself; resizing the children distorts proportions. Replace `currentColor` with hex, then bind the fill to a Variable.
- An empty spacer with HUG on the cross axis stretches to 100 px: make it FIXED 1 px on the cross axis.
- You cannot insert a node into an instance. If there are not enough slots, `detachInstance()` on the container and keep the children as instances.
- Moving children inside a main component keeps the instances' text overrides (IDs do not change), but size overrides break after a `layoutMode` change: reapply them on the instances.

## Name matching
- The script does not check icons (`Icon/…`) and private components (`_…`, `.…`).
- `Parent/Part` (`DataTable/Row`, `Tabs/Item`) is a part of the component `Parent` if there is no such component in code.
- Case and spaces do not matter (the check normalizes them), words do: `Variant=Primary` ↔ `variant="primary"` match, `State=Invalid` ↔ `state="error"` do not.

## What is not suitable for a design system
- `send_code_to_figma` creates a Figma Code layer, not native components. It is fine for a quick screen snapshot, but it is not part of the sync.
- Screenshots as a component source: only as a reference for comparison.

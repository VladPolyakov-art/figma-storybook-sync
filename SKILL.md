---
name: figma-storybook-sync
description: Links a project's design system into one system, Figma ↔ tokens ↔ code ↔ Storybook, and keeps it in sync without Code Connect. The skill moves tokens into Figma Variables, builds Figma components with the same names as in code, and adds links in both directions (the Design panel in Storybook; description and links on the component in Figma). It also sets up drift checking, a GitHub Actions CI check, and Storybook publishing to Chromatic. Use it when the user wants to connect Figma to code or Storybook, sync design tokens or Variables, "make the Figma and Storybook components the same", set up design system sync or a drift check, or bring up a design system on a new project. Use it also when they have no Organization/Enterprise plan for Code Connect, and on phrases like "Figma ↔ code", "tokens.json in Figma", "addon-designs", "Chromatic", "the design system has drifted". It also serves Russian-speaking users.
---

# Figma ↔ tokens ↔ code ↔ Storybook

The goal is one design system in three places that do not drift apart silently. The designer, the developer and the AI agent see the same components with the same names. A check catches any drift, not a person in review.

The method comes from `product-builder/directives/_figma_sync.md` and was verified on a real project (322 Variables, 54 components). The script `scripts/figma_sync.py` is a copy of it with configurable paths and an `init-tokens` command.

## Model (explain to the user at the start, in one paragraph)

1. **Source of truth is code:** `tokens.json` (W3C DTCG) and the components in the repository. Figma is a synchronized copy. A designer's edit in Figma is a proposal: it goes through the drift check and user confirmation, and only then lands in code. Otherwise the system has two sources, and it is unclear who is right in a conflict.
2. **Names match everywhere, instead of a props mapping table.** Variable `color/gray/900` ↔ CSS `--color-gray-900`. Component `Button` in Figma ↔ `Button.tsx` ↔ story `Components/Button`. A variant property ↔ a prop with the same name: `Variant=Primary` ↔ `variant="primary"`. Case and spaces do not matter, words do. A mapping table has to be maintained by hand, while the script checks that names are equal.
3. **The link is two-way without Code Connect.** In Storybook every component has a Design tab with a Figma node (`@storybook/addon-designs`). In Figma the component's `description` holds the code path and story id, and `documentationLinks` holds the URL of the deployed Storybook. Code Connect is added only on an Organization/Enterprise plan.
4. **The drift check is automatic:** locally through MCP (`check`: tokens and components), in CI through REST (`fetch-rest` -> `check --only components`). Below Enterprise, REST does not return Variables, so in CI tokens are checked against the generated CSS, and the full Variables check is done through MCP.

## What not to do

- **Do not touch secrets.** `FIGMA_TOKEN` (a personal Figma token, scope `file_content:read`) and `CHROMATIC_PROJECT_TOKEN` are set by the user: in GitHub -> Settings -> Secrets and variables -> Actions, and locally with `export` in their own terminal. Do not ask for them in chat, write them to files, or print them. If a token is needed for a local step, give the command and let the user run it.
- **No commits or pushes** until the user asks.
- **Do not write to Figma before the plan is approved.** Redrawing costs calls and time, while editing the plan text is free.
- **Load the `figma:figma-use` skill before every `use_figma`.** Before the first write, read `references/figma-gotchas.md`: it has the limits (48 KB response, no `loadAllPagesAsync`, no `throw` at the end) and the Plugin API pitfalls.
- **Do not resolve a conflict silently** (the same thing changed in both code and Figma): show both versions and ask.
- **Do not draw in Figma "on your own".** Only what already exists in code or is approved by the user goes into the library.

## Process

### Step 0. Reconnaissance (read only)

Find out and note briefly:
- **Stack:** the framework, where the components live (layout `<dir>/**/<Name>/<Name>.tsx`?), whether there is Storybook and which version, how tokens are organized now (CSS variables, Tailwind theme, `tokens.json`, Style Dictionary), whether `.github/workflows` exists.
- **Figma:** whether there is a library file, its link or `fileKey`, the Variables collections and their modes, whether there are ComponentSets. Through MCP: `whoami` (team and plan), `get_metadata` for the components page.
- **Where we start from** decides the first step:
  - **code-first** (tokens and components already in code) -> the usual path, F1;
  - **Figma-first** (the design system lives only in Figma, there is no code or it is not token-based) -> `init-tokens` first, then components in code;
  - **nothing exists** -> this is a task of building a design system, not syncing one; say so and offer to build the foundation first.
- **Figma plan:** MCP needs Professional+ with a Dev/Full seat. Code Connect needs Organization/Enterprise. On Starter, warn about MCP limits.

If Figma MCP is not connected, say that writing and the full drift check are unavailable until the user connects it (`/mcp` in terminal `claude`, or the Figma connector in settings). Reconnaissance of code and CI can continue.

### Step 1. Plan -> confirmation

Show the plan in one message: what was found, where we start from, which files will appear or change, what the user does themselves (secrets, the Chromatic project, the Storybook deploy), and what is unavailable on their plan. Put the list of name mismatches between Figma and code in the plan right away: they have to be resolved before building. Wait for a "yes". Do not go further without confirmation.

### Step 2. Script into the repository

CI cannot see `~/.claude`, so the script is placed in the project:
```bash
mkdir -p scripts/ds && cp ~/.claude/skills/figma-storybook-sync/scripts/figma_sync.py scripts/ds/
```
Is the layout different from product-builder (`tokens/tokens.json`, `ds/components.md`, `app/src/components`, `app/src/stories/figma-links.ts`, `ds/figma-map.json`, `.tmp/figma`)? Then create `figma-sync.json` in the root with the keys you need:
```json
{ "paths": {
    "tokens": "design-tokens/tokens.json",
    "components_dir": "src/components",
    "components_doc": "docs/components.md",
    "storybook_index": "storybook-static/index.json",
    "figma_links": "src/stories/figma-links.ts",
    "figma_map": "figma-map.json",
    "out": ".tmp/figma"
} }
```
A key missing from the file takes its default. `components_doc` (the component catalog in markdown) is optional: without it the set of components is checked against the code. `.tmp/` goes in `.gitignore`. All commands from here on: `python3 scripts/ds/figma_sync.py <command> --root .`; help is `--help`.

### Step 3. Tokens

**Figma-first, once:** `export-js` -> run `.tmp/figma/export.js` through `use_figma` -> save the response to `.tmp/figma/export.json`. Save a large response in parts to `.tmp/figma/export/*.json` and pass the folder as `--figma`. Then `init-tokens --figma .tmp/figma/export.json` -> `check --figma … --only tokens` must be green. If the collections are not named `primitives` / `semantic`, use `--primitives-name` / `--semantic-name` or `"collections"` in the map. Move composite tokens (typography, shadow) over from TextStyles/EffectStyles by hand. From this point the source is `tokens.json`.

**Tokens -> CSS:** if the project has no token build, add Style Dictionary (or the project's existing tool) and an `npm run tokens:build` script. It generates CSS variables named by the rule "path joined with hyphens" (`semantic.surface.default` -> `--surface-default`, primitives -> `--color-gray-900`). Components use only variables, not hex. The generated file is committed: CI checks that it is not stale.

**Tokens -> Figma Variables (F1):** `push-variables-js` -> each `.tmp/figma/push-variables*.js` in order through `use_figma`. The write is idempotent, so a repeat run breaks nothing. Then `export-js` -> `check`. Zero drift -> move on.

### Step 4. Components (F2)

One component per `use_figma` call:
1. The reference for appearance is the `AllVariants` story (or a screenshot of all variants from Storybook). In Figma, a ComponentSet on the `Components` page. Variant properties and their values are named like props in code. All fills, strokes, radii and spacing are bound to Variables, texts to TextStyles.
2. The component `description`: purpose, `Code: <path to .tsx>`, `Story: <title>`. Until Storybook is deployed, a local link `localhost:6006/?path=/docs/<id>`. `documentationLinks` only after deployment (Chromatic gives a permanent URL).
3. `get_screenshot` next to the reference. Fix visual differences in Figma, not in code: code is the source.
4. After all components: `export-js` -> `map --figma …` -> a name -> nodeId map. Manual edits in the map are not overwritten, and `"manual": true` protects the whole entry. Then `storybook-links` -> `figma-links.ts`.
5. **Storybook:** install `@storybook/addon-designs` and wire the links in `.storybook/preview.tsx` through `argTypesEnhancers`. A ready, verified example is `assets/storybook-preview.reference.tsx`: `import.meta.glob` so the build does not fail without the file, and `parameters.design` filled in by title. Add `storybook-links` to `prestorybook` and `prebuild-storybook`.
6. **Code Connect** only if the plan allows it: `code-connect` -> `send_code_connect_mappings`. On an access error, mark it "unavailable on this plan" and continue: the links from items 2 and 5 already provide the connection.

### Step 5. CI and Chromatic

Copy `assets/ds.yml` to `.github/workflows/ds.yml` and replace `__APP_DIR__` and `__SYNC__`. If you changed `out` in `figma-sync.json`, fix the `.tmp/figma/...` paths in the workflow. Workflow steps: tokens built -> Storybook builds -> link to Figma (REST) -> Chromatic. Without a secret the step is skipped with a warning instead of failing, so CI is green before the user sets the secrets. Add the npm scripts `figma:check` (MCP export) and `ds:check` (REST) so the drift check can be run with one command.

Give the user instructions for what to do themselves: create a project on chromatic.com (free plan), add two secrets in GitHub, and optionally install the Storybook Connect plugin in Figma. Do not ask for token values in chat.

### Step 6. Readiness check

Run it and show the result, not a retelling:
- `check` through the MCP export: 0 drift in tokens and components;
- `npm run build-storybook` builds, and a component's Design tab opens the Figma node (check in the browser);
- the component in Figma has a `description` with the code path;
- the workflow is valid (`actionlint` if available, otherwise a careful read).

Does the user already have the `FIGMA_TOKEN` secret? Then they can run the REST step locally themselves: `FIGMA_TOKEN=… python3 scripts/ds/figma_sync.py fetch-rest --root .`, then `check --figma .tmp/figma/rest.json --only components`.

## Keeping it in sync (F4): on every design system change

- **Code changed** (`tokens.json` or a component) -> `push-variables-js` or a targeted component edit in Figma -> `export-js` -> `check` -> `map` and `storybook-links` if a component was added.
- **Figma changed** (by a designer) -> `export-js` -> `check`:
  - token values -> `pull-variables --figma …` shows a diff -> user confirmation -> `--apply` -> `tokens:build`;
  - component structure (a new variant, a prop) -> this is a task for code. Code and story first, then F2 for that component.
- **A node from the map was deleted in Figma** -> `check` marks it broken: rebuild the component or remove it from the map.
- Open drift is in `.tmp/figma/drift.md`. Tell the user what remains and why.

## Final report to the user

Short, in plain words, without jargon where possible (reply in the user's language):
- what is linked (how many Variables, how many components, links work in both directions);
- what was checked and how (the `check` result, the Storybook build);
- what is left for them to do (secrets, Chromatic, deploy) and what is unavailable on their plan;
- open drift, if any.

## Skill files

- `scripts/figma_sync.py` generates JS for `use_figma`, parses responses, runs the drift check, and writes the map and links. Stdlib only; only `fetch-rest` uses the network. Commands: `push-variables-js`, `export-js`, `check`, `init-tokens`, `pull-variables`, `map`, `storybook-links`, `code-connect`, `fetch-rest`.
- `scripts/tests/`: run `python3 -m unittest tests/test_figma_sync.py` from `scripts/`. Run it after editing the script.
- `references/figma-gotchas.md`: plans, MCP limits and Plugin API pitfalls. Read before the first write to Figma.
- `assets/ds.yml`: GitHub Actions template.
- `assets/storybook-preview.reference.tsx`: a verified wiring of the Design tab (Storybook 10, react-vite). Adapt it to the project, do not copy blindly: the style import and the decorator there come from product-builder.

# figma-storybook-sync

A Claude Code skill that keeps one design system in sync across `tokens.json`, Figma Variables, code components and Storybook. An automated drift check catches any mismatch between them. No Code Connect needed, so it works on the Figma Professional plan.

## How it works

- **Source of truth is code:** `tokens.json` (W3C DTCG) and the components in the repository. Figma is a synchronized copy. A designer's edit in Figma counts as a proposal: it goes through the drift check and confirmation, and only then lands in code.
- **Names match everywhere** instead of a mapping table:
  - Variable `color/gray/900` ↔ CSS variable `--color-gray-900`;
  - component `Button` in Figma ↔ `Button.tsx` ↔ story `Components/Button`;
  - variant `Variant=Primary` ↔ prop `variant="primary"`.
- **Links in both directions:**
  - in Storybook, each component has a Design tab with a Figma node (`@storybook/addon-designs`);
  - in Figma, the component's `description` holds the code and story path, and `documentationLinks` holds the Storybook link.
- **Drift check:**
  - locally through Figma MCP: tokens and components;
  - in CI through the Figma REST API: components (on plans below Enterprise, REST does not return Variables).

## Process

1. **Reconnaissance, read only.** Stack, Storybook, tokens, CI, the Figma file and plan. Where we start from: code, Figma or nothing.
2. **Plan → confirmation.** Until a "yes", nothing changes in the project or in Figma.
3. **Tokens.**
   - If the design system lives only in Figma, import first: Figma → `tokens.json`.
   - Build the CSS variables.
   - Write the Variables to Figma.
4. **Components.** One at a time in Figma with the same names and props: everything is bound to Variables, then checked against a screenshot. Then a "name → Figma node" map and the links for Storybook.
5. **CI and Chromatic.** A GitHub Actions workflow: tokens built, Storybook builds, drift check against Figma, publish to Chromatic. Without secrets the steps are skipped rather than failing.
6. **Maintenance.**
   - Code changed → update Figma.
   - Figma changed → drift check; token values are moved after confirmation, and structural changes become a task for code.

## Contents

| Path | What |
|---|---|
| `SKILL.md` | instructions for the agent |
| `scripts/figma_sync.py` | generates JS for the Figma Plugin API (`use_figma`), parses responses, runs the drift check, writes the map and links. Stdlib-only Python; only `fetch-rest` uses the network |
| `scripts/tests/` | script tests: `cd scripts && python3 -m unittest tests/test_figma_sync.py` |
| `references/figma-gotchas.md` | plans, MCP limits, Plugin API pitfalls |
| `assets/ds.yml` | GitHub Actions template |
| `assets/storybook-preview.reference.tsx` | wiring of the Design tab (Storybook 10, react-vite) |
| `evals/` | scenarios for checking the skill's behavior |

Script commands: `push-variables-js`, `export-js`, `check`, `init-tokens`, `pull-variables`, `map`, `storybook-links`, `code-connect`, `fetch-rest`. Help: `python3 scripts/figma_sync.py --help`.

## Installation

```bash
git clone https://github.com/VladPolyakov-art/figma-storybook-sync ~/.claude/skills/figma-storybook-sync
```

Requirements:
- Claude Code;
- Figma MCP (for writing to Figma and the full drift check), Professional plan or higher with a Dev/Full seat;
- Python 3.

Code Connect is optional. Enable it only on an Organization or Enterprise plan.

## Secrets

`FIGMA_TOKEN` (scope `file_content:read`) and `CHROMATIC_PROJECT_TOKEN` are set by you: in GitHub → Settings → Secrets and variables → Actions, and locally with `export`. The skill does not ask for them and does not write them to files.

## License

[MIT](LICENSE)

---
tags: [figma-storybook-sync]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Our design system currently lives only in Figma, and there are no tokens in code. I already exported the Variables with export.js, and the response is in design/figma-export.json. Make code the source of truth: tokens in the repository, CSS variables generated from them, and a drift check against Figma that can run in CI. I approve the plan in advance, go ahead. Figma MCP is not connected right now, do not run npm install.

Input files: design/figma-export.json, package.json, src/, .storybook/

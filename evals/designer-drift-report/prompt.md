---
tags: [figma-storybook-sync]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Дизайнер что-то поменял в Figma. Свежий экспорт лежит в .tmp/figma/export.json (скрипт синхронизации — execution/figma_sync.py). Что разъехалось между Figma и кодом и что с этим делать? Ничего пока не меняй.

Input files: tokens/, ds/, app/, execution/, .tmp/figma/export.json

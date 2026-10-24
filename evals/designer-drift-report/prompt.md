---
tags: [figma-storybook-sync]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

A designer changed something in Figma. The fresh export is in .tmp/figma/export.json (the sync script is execution/figma_sync.py). What has drifted between Figma and the code, and what should we do about it? Don't change anything yet.

Input files: tokens/, ds/, app/, execution/, .tmp/figma/export.json

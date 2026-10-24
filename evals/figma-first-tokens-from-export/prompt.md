---
tags: [figma-storybook-sync]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

ДС у нас пока живёт только в Figma, в коде токенов нет. Я уже выгрузил Variables через export.js, ответ лежит в design/figma-export.json. Сделай так, чтобы источником правды стал код: токены в репозитории, из них CSS-переменные, и сверка с Figma, которую можно гонять в CI. План заранее одобряю, делай. Figma MCP сейчас не подключён, npm install не запускай.

Input files: design/figma-export.json, package.json, src/, .storybook/

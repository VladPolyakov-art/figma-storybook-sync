# figma-storybook-sync

Скилл для Claude Code. Связывает дизайн-систему в одну систему: токены, Figma Variables, компоненты в коде, Storybook. Расхождения между ними ловит автоматическая сверка. Работает без Code Connect, то есть на тарифе Figma Professional.

*A Claude Code skill that keeps one design system in sync across `tokens.json`, Figma Variables, code components and Storybook — without Code Connect. Instructions are in Russian.*

## Как устроено

- **Источник правды — код:** `tokens.json` (W3C DTCG) и компоненты в репозитории. Figma — синхронизированная копия. Правка дизайнера в Figma считается предложением: она проходит сверку и подтверждение и только потом попадает в код.
- **Имена совпадают везде** вместо таблицы соответствий:
  - Variable `color/gray/900` ↔ CSS-переменная `--color-gray-900`;
  - компонент `Button` в Figma ↔ `Button.tsx` ↔ story `Components/Button`;
  - вариант `Variant=Primary` ↔ проп `variant="primary"`.
- **Ссылки в обе стороны:**
  - в Storybook у компонента вкладка Design с узлом Figma (`@storybook/addon-designs`);
  - в Figma у компонента в `description` путь к коду и story, в `documentationLinks` — ссылка на Storybook.
- **Сверка:**
  - локально через Figma MCP — токены и компоненты;
  - в CI через Figma REST API — компоненты (на тарифах ниже Enterprise REST не отдаёт Variables).

## Процесс

1. **Разведка, только чтение.** Стек, Storybook, токены, CI, файл Figma и тариф. Откуда стартуем: код, Figma или ничего.
2. **План → подтверждение.** До «да» ничего не меняется ни в проекте, ни в Figma.
3. **Токены.**
   - Если ДС живёт только в Figma — сначала импорт: Figma → `tokens.json`.
   - Сборка CSS-переменных.
   - Запись Variables в Figma.
4. **Компоненты.** По одному в Figma с теми же именами и пропсами: всё привязано к Variables, затем сверка по скриншоту. Потом карта «имя → узел Figma» и ссылки для Storybook.
5. **CI и Chromatic.** Workflow GitHub Actions: токены собраны, Storybook собирается, сверка с Figma, публикация в Chromatic. Без секретов шаги пропускаются, а не падают.
6. **Поддержка.**
   - Изменили код → обновить Figma.
   - Изменили Figma → сверка; значения токенов переносятся после подтверждения, структурные изменения становятся задачей на код.

## Состав

| Путь | Что |
|---|---|
| `SKILL.md` | инструкция для агента |
| `scripts/figma_sync.py` | генерирует JS для Figma Plugin API (`use_figma`), разбирает ответы, сверяет, пишет карту и ссылки. Только stdlib Python; в сеть ходит только `fetch-rest` |
| `scripts/tests/` | тесты скрипта: `cd scripts && python3 -m unittest tests/test_figma_sync.py` |
| `references/figma-gotchas.md` | тарифы, лимиты MCP, грабли Plugin API |
| `assets/ds.yml` | шаблон GitHub Actions |
| `assets/storybook-preview.reference.tsx` | подключение вкладки Design (Storybook 10, react-vite) |
| `evals/` | сценарии проверки поведения скилла |

Команды скрипта: `push-variables-js`, `export-js`, `check`, `init-tokens`, `pull-variables`, `map`, `storybook-links`, `code-connect`, `fetch-rest`. Справка: `python3 scripts/figma_sync.py --help`.

## Установка

```bash
git clone https://github.com/VladPolyakov-art/figma-storybook-sync ~/.claude/skills/figma-storybook-sync
```

Нужны:
- Claude Code;
- Figma MCP (для записи в Figma и полной сверки), тариф Professional или выше с Dev/Full seat;
- Python 3.

Code Connect необязателен. Он включается, только если тариф Organization или Enterprise.

## Секреты

`FIGMA_TOKEN` (scope `file_content:read`) и `CHROMATIC_PROJECT_TOKEN` задаёте вы сами: в GitHub → Settings → Secrets and variables → Actions, локально — через `export`. Скилл их не запрашивает и не пишет в файлы.

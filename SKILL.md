---
name: figma-storybook-sync
description: Связывает дизайн-систему проекта в одну систему Figma ↔ токены ↔ код ↔ Storybook и держит её в синхроне без Code Connect. Скилл переносит токены в Figma Variables, собирает компоненты в Figma с теми же именами, что в коде, и ставит ссылки в обе стороны (панель Design в Storybook, описание и ссылки у компонента в Figma). Ещё он настраивает сверку расхождений, CI-проверку в GitHub Actions и публикацию Storybook в Chromatic. Используй, когда пользователь хочет связать Figma с кодом или Storybook, синхронизировать дизайн-токены или Variables, «чтобы компоненты в Figma и в Storybook были одни и те же», настроить design system sync или drift check, поднять ДС на новом проекте. Используй и когда у него нет тарифа Organization/Enterprise для Code Connect, а также при словах «Figma ↔ код», «tokens.json в Figma», «addon-designs», «Chromatic», «ДС разъехалась».
---

# Figma ↔ токены ↔ код ↔ Storybook

Цель — одна дизайн-система в трёх местах, которые не расходятся молча. Дизайнер, разработчик и AI-агент видят одни и те же компоненты с одними и теми же именами. Любое расхождение ловит проверка, а не человек на ревью.

Методология взята из `product-builder/directives/_figma_sync.md` и проверена на реальном проекте (322 Variables, 54 компонента). Скрипт `scripts/figma_sync.py` — её копия с настраиваемыми путями и командой `init-tokens`.

## Модель (объяснить пользователю в начале, одним абзацем)

1. **Источник правды — код:** `tokens.json` (W3C DTCG) и компоненты в репозитории. Figma — синхронизированная копия. Правка дизайнера в Figma — предложение: она проходит сверку и подтверждение пользователя и только потом попадает в код. Иначе у системы два источника, и непонятно, кто прав при конфликте.
2. **Имена совпадают везде — вместо таблицы соответствий props.** Variable `color/gray/900` ↔ CSS `--color-gray-900`. Компонент `Button` в Figma ↔ `Button.tsx` ↔ story `Components/Button`. Свойство варианта ↔ проп с тем же именем: `Variant=Primary` ↔ `variant="primary"`. Регистр и пробелы не важны, слова важны. Таблицу соответствий надо поддерживать руками, а одинаковые имена проверяет скрипт.
3. **Связь двусторонняя без Code Connect.** В Storybook у каждого компонента вкладка Design с узлом Figma (`@storybook/addon-designs`). В Figma у компонента в `description` путь к коду и id story, а в `documentationLinks` — URL задеплоенного Storybook. Code Connect добавляется, только если тариф Organization/Enterprise.
4. **Сверка автоматическая:** локально через MCP (`check` — токены и компоненты), в CI через REST (`fetch-rest` → `check --only components`). На не-Enterprise REST не отдаёт Variables, поэтому токены в CI сверяются со сгенерированным CSS, а полную сверку Variables делает MCP.

## Чего не делать

- **Секреты не трогать.** `FIGMA_TOKEN` (личный токен Figma, scope `file_content:read`) и `CHROMATIC_PROJECT_TOKEN` пользователь задаёт сам: в GitHub → Settings → Secrets and variables → Actions, локально — через `export` в своём терминале. Не просить их в чат, не писать в файлы, не выводить. Если токен нужен для локального шага — дать команду, которую пользователь запустит сам.
- **Без коммитов и пушей**, пока пользователь не попросил.
- **Не писать в Figma до утверждения плана.** Перерисовка стоит вызовов и времени, правка текста плана бесплатна.
- **Перед каждым `use_figma` загрузить скилл `figma:figma-use`.** Перед первой записью прочитать `references/figma-gotchas.md` — там лимиты (48 КБ ответа, запрет `loadAllPagesAsync`, никакого `throw` в конце) и грабли Plugin API.
- **Конфликт** (одно и то же поменяли и в коде, и в Figma) не решать молча: показать обе версии и спросить.
- **Не рисовать в Figma «от себя».** В библиотеку попадает только то, что уже есть в коде или утверждено пользователем.

## Процесс

### Шаг 0. Разведка (только чтение)

Выясни и запиши коротко:
- **Стек:** фреймворк, где компоненты (раскладка `<dir>/**/<Name>/<Name>.tsx`?), есть ли Storybook и какой версии, как сейчас устроены токены (CSS-переменные, Tailwind theme, `tokens.json`, Style Dictionary), есть ли `.github/workflows`.
- **Figma:** есть ли файл с библиотекой, ссылка или `fileKey`, коллекции Variables и их режимы, есть ли ComponentSet'ы. Через MCP: `whoami` (команда и тариф), `get_metadata` по странице компонентов.
- **Откуда стартуем** — от этого зависит первый шаг:
  - **код-first** (токены и компоненты уже в коде) → обычный путь, F1;
  - **Figma-first** (ДС живёт только в Figma, кода нет или он не на токенах) → сначала `init-tokens`, потом компоненты в коде;
  - **ничего нет** → это задача построения ДС, а не синхронизации; сказать об этом и предложить сначала собрать foundation.
- **Тариф Figma:** Professional+ с Dev/Full seat нужен для MCP. Organization/Enterprise — для Code Connect. На Starter предупредить про лимиты MCP.

Если Figma MCP не подключён — сказать, что запись и полная сверка недоступны, пока пользователь не подключит его (`/mcp` в терминальном `claude` или коннектор Figma в настройках). Разведку кода и CI можно продолжать.

### Шаг 1. План → подтверждение

Покажи план одним сообщением: что найдено, откуда стартуем, какие файлы появятся или изменятся, что пользователь делает сам (секреты, Chromatic-проект, деплой Storybook), что недоступно на его тарифе. Список расхождений имён между Figma и кодом — сразу в план: их придётся решить до сборки. Жди «да». Без подтверждения дальше не идём.

### Шаг 2. Скрипт в репозиторий

CI не видит `~/.claude`, поэтому скрипт кладём в проект:
```bash
mkdir -p scripts/ds && cp ~/.claude/skills/figma-storybook-sync/scripts/figma_sync.py scripts/ds/
```
Раскладка отличается от product-builder (`tokens/tokens.json`, `ds/components.md`, `app/src/components`, `app/src/stories/figma-links.ts`, `ds/figma-map.json`, `.tmp/figma`)? Тогда создай в корне `figma-sync.json` с нужными ключами:
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
Ключ, которого нет в файле, берётся по умолчанию. `components_doc` (каталог компонентов в markdown) необязателен: без него сверка состава идёт по коду. `.tmp/` — в `.gitignore`. Все команды дальше: `python3 scripts/ds/figma_sync.py <команда> --root .`, справка — `--help`.

### Шаг 3. Токены

**Figma-first, один раз:** `export-js` → выполнить `.tmp/figma/export.js` через `use_figma` → сохранить ответ в `.tmp/figma/export.json`. Большой ответ сохраняй по частям в `.tmp/figma/export/*.json` и передавай `--figma` папку. Затем `init-tokens --figma .tmp/figma/export.json` → `check --figma … --only tokens` должен быть зелёным. Коллекции называются не `primitives` / `semantic` → `--primitives-name` / `--semantic-name` или `"collections"` в карте. Составные токены (typography, shadow) перенести из TextStyles/EffectStyles руками. С этого момента источник — `tokens.json`.

**Токены → CSS:** если в проекте нет сборки токенов, добавь Style Dictionary (или существующий инструмент проекта) и скрипт `npm run tokens:build`. Он генерирует CSS-переменные с именами по правилу «путь через дефис» (`semantic.surface.default` → `--surface-default`, примитивы — `--color-gray-900`). Компоненты используют только переменные, а не hex. Сгенерированный файл коммитится: CI проверяет, что он не устарел.

**Токены → Figma Variables (F1):** `push-variables-js` → каждый `.tmp/figma/push-variables*.js` по порядку через `use_figma`. Запись идемпотентная, повторный прогон ничего не ломает. Потом `export-js` → `check`. Ноль расхождений → дальше.

### Шаг 4. Компоненты (F2)

По одному компоненту за вызов `use_figma`:
1. Эталон вида — story `AllVariants` (или скриншот всех вариантов из Storybook). В Figma ComponentSet на странице `Components`. Свойства вариантов и их значения называются как пропсы в коде. Все fills, strokes, радиусы и отступы привязаны к Variables, тексты — к TextStyles.
2. `description` компонента: назначение, `Code: <путь к .tsx>`, `Story: <title>`. Пока Storybook не задеплоен — локальная ссылка `localhost:6006/?path=/docs/<id>`. `documentationLinks` — только после деплоя (Chromatic даёт постоянный URL).
3. `get_screenshot` рядом с эталоном. Расхождение вида правим в Figma, не в коде: код — источник.
4. После всех компонентов: `export-js` → `map --figma …` → карта имя → nodeId. Ручные правки в карте не затираются, `"manual": true` защищает запись целиком. Затем `storybook-links` → `figma-links.ts`.
5. **Storybook:** поставить `@storybook/addon-designs` и подключить ссылки в `.storybook/preview.tsx` через `argTypesEnhancers`. Готовый проверенный пример — `assets/storybook-preview.reference.tsx`: `import.meta.glob`, чтобы сборка не падала без файла, и подстановка `parameters.design` по title. Добавить `storybook-links` в `prestorybook` и `prebuild-storybook`.
6. **Code Connect** — только если тариф позволяет: `code-connect` → `send_code_connect_mappings`. Ошибка доступа → отметить «недоступен на тарифе» и продолжить: ссылки из пунктов 2 и 5 уже дают связь.

### Шаг 5. CI и Chromatic

Скопируй `assets/ds.yml` в `.github/workflows/ds.yml` и замени `__APP_DIR__` и `__SYNC__`. Если в `figma-sync.json` поменял `out` — поправь пути `.tmp/figma/...` в workflow. Шаги workflow: токены собраны → Storybook собирается → связь с Figma (REST) → Chromatic. Без секрета шаг пропускается с warning, а не падает: так CI зелёный до того, как пользователь задал секреты. Добавь npm-скрипты `figma:check` (MCP-экспорт) и `ds:check` (REST), чтобы сверку можно было запустить одной командой.

Пользователю — инструкция, что сделать самому: создать проект на chromatic.com (бесплатный тариф), добавить два секрета в GitHub, по желанию поставить в Figma плагин Storybook Connect. Значения токенов в чат не присылать.

### Шаг 6. Проверка готовности

Прогони и покажи результат, а не пересказ:
- `check` через MCP-экспорт: 0 расхождений по токенам и компонентам;
- `npm run build-storybook` собирается, во вкладке Design у компонента открывается узел Figma (проверь в браузере);
- в Figma у компонента есть `description` с путём к коду;
- workflow валиден (`actionlint`, если есть, иначе внимательное чтение).

Секрет `FIGMA_TOKEN` у пользователя уже есть? Тогда он может сам прогнать REST-шаг локально: `FIGMA_TOKEN=… python3 scripts/ds/figma_sync.py fetch-rest --root .`, затем `check --figma .tmp/figma/rest.json --only components`.

## Поддержка синхронизации (F4) — при каждом изменении ДС

- **Изменили код** (`tokens.json` или компонент) → `push-variables-js` или точечная правка компонента в Figma → `export-js` → `check` → `map` и `storybook-links`, если появился компонент.
- **Изменили Figma** (дизайнер) → `export-js` → `check`:
  - значения токенов → `pull-variables --figma …` показывает diff → подтверждение пользователя → `--apply` → `tokens:build`;
  - структура компонента (новый вариант, проп) → это задача на код. Сначала код и story, потом F2 для этого компонента.
- **Узел из карты удалён в Figma** → `check` пометит его битым: пересобрать компонент или убрать из карты.
- Открытые расхождения — в `.tmp/figma/drift.md`. Перечисли пользователю, что осталось и почему.

## Отчёт пользователю в конце

Коротко, по-русски, без жаргона там, где можно обойтись:
- что связано (сколько Variables, сколько компонентов, ссылки работают в обе стороны);
- что проверено и как (результат `check`, сборка Storybook);
- что осталось сделать ему (секреты, Chromatic, деплой) и что недоступно на тарифе;
- открытые расхождения, если есть.

## Файлы скилла

- `scripts/figma_sync.py` — генерирует JS для `use_figma`, разбирает ответы, сверяет, пишет карту и ссылки. Только stdlib, в сеть ходит только `fetch-rest`. Команды: `push-variables-js`, `export-js`, `check`, `init-tokens`, `pull-variables`, `map`, `storybook-links`, `code-connect`, `fetch-rest`.
- `scripts/tests/` — `python3 -m unittest tests/test_figma_sync.py` из `scripts/`. Запусти после правки скрипта.
- `references/figma-gotchas.md` — тарифы, лимиты MCP и грабли Plugin API. Читать перед первой записью в Figma.
- `assets/ds.yml` — шаблон GitHub Actions.
- `assets/storybook-preview.reference.tsx` — проверенное подключение вкладки Design (Storybook 10, react-vite). Адаптировать к проекту, не копировать вслепую: импорт стилей и декоратор там из product-builder.

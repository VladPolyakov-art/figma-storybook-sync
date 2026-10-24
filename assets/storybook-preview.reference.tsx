import type { Preview } from "@storybook/react-vite";
import "../src/styles/index.css";

/* Ссылки на Figma для вкладки Design (@storybook/addon-designs): src/stories/figma-links.ts генерирует
   `npm run figma:links` из ds/figma-map.json. import.meta.glob — если файла нет, сборка не падает (будет {}). */
const linkModules = import.meta.glob<{ figmaLinks?: Record<string, string> }>("../src/stories/figma-links.ts", { eager: true });
const figmaLinks: Record<string, string> = Object.values(linkModules)[0]?.figmaLinks ?? {};
const figmaUrl = (title: string) => figmaLinks[title] ?? figmaLinks[title.split("/").pop() ?? ""];

/* Фон холста — surface-canvas, как на экранах. Порядок разделов = ORDER в src/review/entries.ts. */
const preview: Preview = {
  parameters: {
    layout: "padded",
    controls: { matchers: { color: /(background|color)$/i, date: /Date$/i } },
    a11y: { test: "todo" },
    options: { storySort: { order: ["Foundation", "Components", "Patterns", "Shell", "Sandboxes", "Screens"] } },
  },
  /* parameters.design по title истории (полный title или последний сегмент = имя-якорь). Ставим в argTypesEnhancer:
     он получает тот же объект parameters, что уходит в панель Design (декоратор для этого поздно — панель читает
     parameters до рендера). Своё parameters.design в истории главнее. Проверено на Storybook 10.6 (prepareStory). */
  argTypesEnhancers: [
    (context) => {
      const url = figmaUrl(context.title);
      if (url && !context.parameters.design) context.parameters.design = { type: "figma", url };
      return context.argTypes;
    },
  ],
  decorators: [(Story) => <div style={{ background: "var(--surface-canvas)", padding: "var(--space-16)" }}><Story /></div>],
};
export default preview;

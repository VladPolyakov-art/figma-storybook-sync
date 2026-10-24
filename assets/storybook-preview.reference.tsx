import type { Preview } from "@storybook/react-vite";
import "../src/styles/index.css";

/* Figma links for the Design tab (@storybook/addon-designs): src/stories/figma-links.ts is generated
   by `npm run figma:links` from ds/figma-map.json. With import.meta.glob the build does not fail if the file is missing (it becomes {}). */
const linkModules = import.meta.glob<{ figmaLinks?: Record<string, string> }>("../src/stories/figma-links.ts", { eager: true });
const figmaLinks: Record<string, string> = Object.values(linkModules)[0]?.figmaLinks ?? {};
const figmaUrl = (title: string) => figmaLinks[title] ?? figmaLinks[title.split("/").pop() ?? ""];

/* Canvas background is surface-canvas, as on the screens. Section order = ORDER in src/review/entries.ts. */
const preview: Preview = {
  parameters: {
    layout: "padded",
    controls: { matchers: { color: /(background|color)$/i, date: /Date$/i } },
    a11y: { test: "todo" },
    options: { storySort: { order: ["Foundation", "Components", "Patterns", "Shell", "Sandboxes", "Screens"] } },
  },
  /* parameters.design by story title (full title or last segment = anchor name). Set in an argTypesEnhancer:
     it receives the same parameters object that goes to the Design panel (a decorator is too late, because the panel
     reads parameters before rendering). A story's own parameters.design takes priority. Verified on Storybook 10.6 (prepareStory). */
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

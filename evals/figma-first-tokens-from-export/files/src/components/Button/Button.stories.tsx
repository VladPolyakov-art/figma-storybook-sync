import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button } from "./Button";

const meta: Meta<typeof Button> = {
  title: "Components/Button",
  component: Button,
  argTypes: { variant: { control: "select", options: ["primary", "secondary", "ghost"] } },
  args: { children: "Save" },
};
export default meta;
export const Primary: StoryObj<typeof Button> = { args: { variant: "primary" } };
export const Ghost: StoryObj<typeof Button> = { args: { variant: "ghost" } };

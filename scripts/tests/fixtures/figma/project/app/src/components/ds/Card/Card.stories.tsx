import type { Meta, StoryObj } from "@storybook/react-vite";
import { Card } from "./Card";

const meta = { title: "Components/Card", component: Card } satisfies Meta<typeof Card>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Elevated: Story = { name: "Elevated" };

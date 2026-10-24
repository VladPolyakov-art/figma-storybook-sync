import type { Meta, StoryObj } from "@storybook/react-vite";
import { Badge } from "./Badge";

const meta: Meta<typeof Badge> = { title: "Components/Badge", component: Badge, args: { children: "Active" } };
export default meta;
export const Neutral: StoryObj<typeof Badge> = {};

import "./Button.css";

export type ButtonProps = {
  variant?: "primary" | "secondary" | "ghost";
  size?: "sm" | "md";
  disabled?: boolean;
  children: React.ReactNode;
};

export function Button({ variant = "primary", size = "md", disabled, children }: ButtonProps) {
  return <button className={`btn btn--${variant} btn--${size}`} disabled={disabled}>{children}</button>;
}

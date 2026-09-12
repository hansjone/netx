import type { ReactNode, SelectHTMLAttributes } from "react";
import { Label } from "@heroui/react";

/** Compact labeled native select — HeroUI Select is heavy for dense ops forms. */
export function FieldSelect({
  label,
  hint,
  required,
  fullWidth,
  className = "",
  children,
  ...props
}: {
  label?: ReactNode;
  hint?: ReactNode;
  required?: boolean;
  fullWidth?: boolean;
  className?: string;
  children: ReactNode;
} & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <label className={`ui-field${fullWidth ? " ui-field--full" : ""}${className ? ` ${className}` : ""}`}>
      {label ? (
        <Label isRequired={required} className="ui-field__label">
          {label}
        </Label>
      ) : null}
      <select className="ui-field__select" required={required} {...props}>
        {children}
      </select>
      {hint ? <span className="ui-field__hint">{hint}</span> : null}
    </label>
  );
}

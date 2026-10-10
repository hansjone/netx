import { useId, type ReactNode, type SelectHTMLAttributes } from "react";
import { Label } from "@heroui/react";

/** Compact labeled native select — HeroUI Select is heavy for dense ops forms. */
export function FieldSelect({
  label,
  hint,
  required,
  fullWidth,
  className = "",
  children,
  id,
  "aria-describedby": describedBy,
  ...props
}: {
  label?: ReactNode;
  hint?: ReactNode;
  required?: boolean;
  fullWidth?: boolean;
  className?: string;
  children: ReactNode;
} & SelectHTMLAttributes<HTMLSelectElement>) {
  const generatedId = useId();
  const selectId = id || generatedId;
  const hintId = `${selectId}-hint`;
  return (
    <div className={`ui-field${fullWidth ? " ui-field--full" : ""}${className ? ` ${className}` : ""}`}>
      {label ? (
        <Label htmlFor={selectId} isRequired={required} className="ui-field__label">
          {label}
        </Label>
      ) : null}
      <select
        className="ui-field__select"
        required={required}
        {...props}
        id={selectId}
        aria-describedby={[describedBy, hint ? hintId : null].filter(Boolean).join(" ") || undefined}
      >
        {children}
      </select>
      {hint ? <span id={hintId} className="ui-field__hint">{hint}</span> : null}
    </div>
  );
}

"use client";
// A number box with min, max, step and a unit shown inside it ("Rs", "%", "lots"). The value is a number, or
// "" while the box is empty. Out-of-range values are allowed while typing; the field shows why they are wrong.
//
//   <NumberInput id="cap" value={capital} onChange={setCapital} min={10000} step={10000} unit="Rs" />
import { Input } from "@/components/ui/input";

// The problem with a value, or "" when it is fine (for FormField's error).
export function numberProblem(value, { min, max, required = true } = {}) {
  if (value === "" || value === null || value === undefined) return required ? "Enter a number." : "";
  const n = Number(value);
  if (!Number.isFinite(n)) return "Enter a number.";
  if (min !== undefined && n < min) return `At least ${min}.`;
  if (max !== undefined && n > max) return `At most ${max}.`;
  return "";
}

export default function NumberInput({ id, value, onChange, min, max, step = 1, unit, placeholder, disabled,
  className = "", invalid, ...rest }) {
  const problem = invalid ?? Boolean(numberProblem(value, { min, max, required: false }));
  return (
    <div className={`relative ${className}`}>
      <Input id={id} type="number" inputMode="decimal" min={min} max={max} step={step} placeholder={placeholder}
        disabled={disabled} value={value ?? ""} aria-invalid={problem || undefined}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        className={`num ${unit ? "pr-12" : ""}`} {...rest} />
      {unit && (
        <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-muted-foreground">
          {unit}
        </span>
      )}
    </div>
  );
}

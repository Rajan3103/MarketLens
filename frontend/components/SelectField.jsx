"use client";
// A drop-down with its label, built on shadcn Select.
//
//   <SelectField label="Underlying" value={u} onChange={setU}
//     options={["NIFTY", "BANKNIFTY"]} />                      or options={[{ value: "pct", label: "% of price" }]}
import FormField from "@/components/FormField";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

// "NIFTY" or { value, label } -> { value, label } (values become text, as Select needs).
function optionOf(o) {
  return typeof o === "object" ? { value: String(o.value), label: o.label ?? String(o.value) } : { value: String(o), label: String(o) };
}

export default function SelectField({ id, label, value, onChange, options = [], placeholder = "Pick one", unit, help,
  hint, error, disabled, className = "" }) {
  const items = options.map(optionOf);
  const select = (
    <Select value={value === undefined || value === null ? undefined : String(value)} onValueChange={onChange}
      disabled={disabled}>
      <SelectTrigger id={id} className="w-full" aria-invalid={Boolean(error) || undefined}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {items.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
  if (!label) return select;
  return (
    <FormField label={label} unit={unit} help={help} hint={hint} error={error} htmlFor={id} className={className}>
      {select}
    </FormField>
  );
}

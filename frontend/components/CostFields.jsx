"use client";
// The "Trading costs" boxes every backtest, simulator and paper-trading form shows. The list comes from the
// backend (GET /api/settings/cost-fields = COST_FIELDS in marketlens/costs.py), so it always matches Streamlit's
// and the checks in costs.cost_settings_from_dict. Send `value` to the API as "costs".
//
//   const [costs, setCosts] = useState({});
//   <CostFields value={costs} onChange={setCosts} />            // {} = the defaults
import { ChevronDown, Wallet } from "lucide-react";
import { useState } from "react";

import ErrorBox from "@/components/ErrorBox";
import FormField from "@/components/FormField";
import Loading from "@/components/Loading";
import NumberInput, { numberProblem } from "@/components/NumberInput";
import SelectField from "@/components/SelectField";
import SwitchField from "@/components/SwitchField";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useApi } from "@/lib/queries";

// One box, drawn by its kind (number, bool or choice).
function CostBox({ field, value, onChange, slippageType }) {
  const id = `cost-${field.name}`;
  if (field.kind === "bool") {
    return <SwitchField id={id} label={field.label} hint={field.help} checked={value} onChange={onChange} />;
  }
  if (field.kind === "choice") {
    return (
      <SelectField id={id} label={field.label} help={field.help} value={value} onChange={onChange}
        options={Object.entries(field.choices).map(([v, label]) => ({ value: v, label }))} />
    );
  }
  // slippage: its unit follows the "Slippage in" choice
  const unit = field.name === "slippage_value" ? (slippageType === "points" ? "Rs" : "%") : field.unit;
  return (
    <FormField label={field.label} unit={unit} help={field.help} htmlFor={id}
      error={numberProblem(value, { min: field.min })}>
      <NumberInput id={id} value={value} onChange={onChange} min={field.min} step={field.step} />
    </FormField>
  );
}

export default function CostFields({ value = {}, onChange, defaultOpen = false }) {
  const { data, error, refresh } = useApi("settings/cost-fields");
  const [open, setOpen] = useState(defaultOpen);
  const fields = data?.fields || [];
  const current = Object.fromEntries(fields.map((f) => [f.name, value[f.name] ?? f.default]));
  const set = (name, v) => onChange({ ...current, [name]: v });
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-xl border bg-card">
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium">
        <Wallet className="size-4 text-primary" aria-hidden="true" />
        <span className="flex-1">Trading costs (brokerage, taxes, slippage)</span>
        <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t px-4 py-4">
        {error ? <ErrorBox error={error} onRetry={refresh} />
          : !data ? <Loading text="Loading the cost boxes…" />
            : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {fields.map((f) => (
                  <CostBox key={f.name} field={f} value={current[f.name]} slippageType={current.slippage_type}
                    onChange={(v) => set(f.name, v)} />
                ))}
              </div>
            )}
      </CollapsibleContent>
    </Collapsible>
  );
}

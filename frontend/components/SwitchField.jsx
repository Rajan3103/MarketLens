"use client";
// An on / off switch with its label and an optional line of help under it.
//
//   <SwitchField id="tax" label="Include taxes" checked={tax} onChange={setTax} hint="STT, stamp, GST…" />
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

export default function SwitchField({ id, label, checked, onChange, hint, disabled, className = "" }) {
  return (
    <div className={`flex items-start gap-3 ${className}`}>
      <Switch id={id} checked={Boolean(checked)} onCheckedChange={onChange} disabled={disabled} className="mt-0.5" />
      <div className="space-y-0.5">
        <Label htmlFor={id}>{label}</Label>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
    </div>
  );
}

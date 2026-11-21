"use client";
// A red button for things that cannot be undone. It opens a confirm dialog; for live actions (kill switch,
// square off, real orders) the user must also type a word such as STOP before the button works.
//
//   <DangerButton label="Kill switch ON" tick="Every new order is blocked for the rest of the day."
//     confirmWord="STOP" onConfirm={() => apiPost("live/kill", { confirm: "KILL" })} />
//
// tick: what will happen (shown in the dialog). onConfirm may return a promise; the dialog waits for it.
import { Loader2, TriangleAlert } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader,
  AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function DangerButton({ label, tick, title, onConfirm, confirmWord, disabled = false, size }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const wordOk = !confirmWord || typed.trim().toUpperCase() === confirmWord.toUpperCase();

  // Open or close the dialog (always starting with an empty word box).
  const change = (next) => {
    if (busy) return;
    setOpen(next);
    setTyped("");
  };

  // The final "Yes" in the dialog.
  const confirm = async () => {
    setBusy(true);
    try {
      await onConfirm();
      setOpen(false);
      setTyped("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={change}>
      <AlertDialogTrigger asChild>
        <Button variant="destructive" size={size} disabled={disabled}>
          <TriangleAlert aria-hidden="true" /> {label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title || `${label}?`}</AlertDialogTitle>
          <AlertDialogDescription>{tick || "This cannot be undone."}</AlertDialogDescription>
        </AlertDialogHeader>
        {confirmWord && (
          <div className="space-y-2">
            <Label htmlFor="danger-word">Type <span className="font-mono font-bold">{confirmWord}</span> to confirm</Label>
            <Input id="danger-word" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off"
              autoFocus onKeyDown={(e) => { if (e.key === "Enter" && wordOk && !busy) confirm(); }} />
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <Button onClick={confirm} disabled={!wordOk || busy}
            className="bg-loss text-loss-foreground hover:bg-loss/90">
            {busy && <Loader2 className="animate-spin" aria-hidden="true" />} {label}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

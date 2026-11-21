"use client";
// Code or an address in a monospace box with a Copy button (Pine Script, webhook URLs, terminal commands).
//
//   <CodeBlock code={pine} label="Pine Script v6" />      <CodeBlock code={url} inline />
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

export default function CodeBlock({ code = "", label, inline = false, maxHeight = "24rem" }) {
  const [copied, setCopied] = useState(false);

  // Put the text on the clipboard and say so.
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      toast.success(label ? `${label} copied` : "Copied");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy. Select the text and press Ctrl+C.");
    }
  };

  const button = (
    <Button variant="ghost" size="icon-sm" onClick={copy} aria-label={copied ? "Copied" : "Copy"}>
      {copied ? <Check className="text-profit" /> : <Copy />}
    </Button>
  );
  if (inline) {
    return (
      <div className="flex items-center gap-1 rounded-md border bg-muted pl-3">
        <code className="min-w-0 flex-1 truncate py-1.5 text-sm">{code}</code>
        {button}
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border bg-muted">
      <div className="flex items-center justify-between border-b px-3 py-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        {button}
      </div>
      <pre className="overflow-auto p-3 text-sm leading-relaxed" style={{ maxHeight }}><code>{code}</code></pre>
    </div>
  );
}

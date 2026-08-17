import type * as React from "react";

import { cn } from "@/lib/utils";

export function ChipRow({
  className,
  children,
}: { className?: string; children: React.ReactNode }) {
  return <div className={cn("flex flex-wrap gap-2", className)}>{children}</div>;
}

export function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-secondary py-1 pr-2 pl-3 text-xs text-secondary-foreground">
      <span className="max-w-70 truncate">{label}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${label}`}
        className="text-muted-foreground hover:text-foreground"
      >
        ×
      </button>
    </span>
  );
}

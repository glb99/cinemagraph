import type * as React from "react";

import { cn } from "@/lib/utils";

/** A styled native <select> rather than the Radix listbox shadcn ships: every
 * dropdown in this app is a short, flat list of plain strings (effect names,
 * model ids, project names), which the platform control already handles --
 * including on touch -- without adding @radix-ui/react-select as a dependency. */
export function Select({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "h-9 rounded-md border border-input bg-background px-2 text-sm shadow-sm",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

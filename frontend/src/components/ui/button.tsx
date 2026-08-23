import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      // Soft tint: colour arrives as a wash plus a hairline ring rather than a
      // solid slab. On a screen whose whole point is showing generated images,
      // a saturated block of primary sitting next to the artwork competes with
      // it; a wash reads as clearly actionable without winning that fight.
      // `ring-inset` rather than `border` so the ring costs no layout box and
      // buttons keep lining up with inputs of the same height.
      variant: {
        default: "bg-primary/15 text-primary ring-1 ring-primary/45 ring-inset hover:bg-primary/25",
        secondary: "bg-foreground/5 text-foreground hover:bg-foreground/10",
        outline: "border border-border bg-transparent hover:bg-accent hover:text-accent-foreground",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        destructive:
          "bg-destructive/15 text-destructive ring-1 ring-destructive/45 ring-inset hover:bg-destructive/25",
        // The "keep this one" action. Still deliberately distinct from every
        // other button on the page -- green wash against primary wash, rather
        // than green slab against primary slab. It's the only control whose
        // absence loses work, so the contrast between them has to survive any
        // restyle. See SaveToLibrary.
        success: "bg-success/18 text-success ring-1 ring-success/50 ring-inset hover:bg-success/28",
      },
      size: {
        default: "h-10 px-5 py-2",
        sm: "h-8 px-3 text-xs",
        icon: "h-6 w-6 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

export { buttonVariants };

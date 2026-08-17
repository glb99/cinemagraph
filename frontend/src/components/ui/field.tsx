import type * as React from "react";

import { cn } from "@/lib/utils";

/** The grouped-controls box the old UI drew with <fieldset>/<legend> -- kept as
 * real fieldset/legend elements (not a div with a heading) so the grouping
 * survives for screen readers, with the browser's default border replaced by
 * the token palette. */
export function Fieldset({
  legend,
  className,
  children,
}: {
  legend: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className={cn("rounded-lg border border-border p-4", className)}>
      <legend className="px-2 text-xs font-medium text-muted-foreground">{legend}</legend>
      {children}
    </fieldset>
  );
}

/** Label stacked above its control -- for anything full-width (text inputs,
 * textareas, file pickers). */
export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: the control is the `children` this wraps -- nesting it inside the <label> is what associates the two, but the rule can only see that when the input is written out literally.
    <label className={cn("block space-y-1.5", className)}>
      <span className="block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

/** Label beside its control -- for the narrow numeric knobs that read better in
 * a row (duration, fps, crossfade seconds...). */
export function InlineField({
  label,
  className,
  children,
}: {
  label: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: same as Field above -- the control arrives as children.
    <label className={cn("inline-flex items-center gap-2 text-sm", className)}>
      <span className="whitespace-nowrap">{label}</span>
      {children}
    </label>
  );
}

export function CheckboxField({
  label,
  className,
  ...props
}: React.ComponentProps<"input"> & { label: React.ReactNode }) {
  return (
    <label className={cn("inline-flex items-center gap-2 text-sm", className)}>
      <input
        type="checkbox"
        className="size-4 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        {...props}
      />
      <span>{label}</span>
    </label>
  );
}

export function Hint({ className, children }: { className?: string; children: React.ReactNode }) {
  return <p className={cn("text-xs text-muted-foreground", className)}>{children}</p>;
}

/** Job/validation errors. `whitespace-pre-wrap` because a failing render's
 * `job.error` can be a multi-line traceback-ish string straight from the
 * pipeline, and collapsing it to one line makes it unreadable. */
export function ErrorText({ children }: { children?: React.ReactNode }) {
  if (!children) return null;
  return (
    <p className="whitespace-pre-wrap text-sm text-destructive" role="alert">
      {children}
    </p>
  );
}

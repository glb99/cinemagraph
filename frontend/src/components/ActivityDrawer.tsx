import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { NavIcon } from "@/components/NavIcon";
import { Button } from "@/components/ui/button";
import { type JobRecord, isTerminal, useJobs } from "@/hooks/useJobs";
import { TABS } from "@/lib/tabs";
import { cn } from "@/lib/utils";

const PHASE_LABEL: Record<JobRecord["phase"], string> = {
  submitting: "Submitting",
  pending: "Queued",
  running: "Running",
  done: "Done",
  error: "Failed",
};

const PHASE_DOT: Record<JobRecord["phase"], string> = {
  submitting: "bg-muted-foreground/50",
  pending: "bg-muted-foreground/50",
  running: "bg-primary",
  done: "bg-success",
  error: "bg-destructive",
};

function sourceLabel(source: string) {
  return TABS.find((tab) => tab.to === source)?.label ?? source;
}

function elapsed(record: JobRecord) {
  const end = record.finishedAt ?? Date.now();
  const seconds = Math.max(0, Math.round((end - record.startedAt) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function Row({ record }: { record: JobRecord }) {
  return (
    <li className="flex items-start gap-2.5 px-3 py-2.5">
      <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", PHASE_DOT[record.phase])} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate font-medium text-sm">{sourceLabel(record.source)}</span>
          <span className="shrink-0 text-muted-foreground text-xs">{elapsed(record)}</span>
        </div>
        <p className="text-muted-foreground text-xs">
          {PHASE_LABEL[record.phase]}
          {record.error ? ` — ${record.error}` : null}
        </p>
      </div>
      {/* The tab still owns the preview and the save control, so this points
          there rather than duplicating either. Navigating back now restores
          the finished job instead of finding an empty form. */}
      <Link
        to={record.source}
        className="shrink-0 font-medium text-primary text-xs hover:underline"
      >
        Open
      </Link>
    </li>
  );
}

/** A running job is no longer tied to the tab that started it, so there has to
 * be somewhere to see it from anywhere. Renders nothing until something has
 * actually been submitted -- an always-present "0 jobs" control is furniture. */
export function ActivityDrawer() {
  const { records } = useJobs();
  const [open, setOpen] = useState(false);

  if (records.length === 0) return null;

  const active = records.filter((record) => !isTerminal(record.phase)).length;
  const newest = [...records].reverse();

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        className="w-full justify-start gap-2.5"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <NavIcon name="activity" />
        <span>Activity</span>
        {active > 0 ? (
          <span className="ml-auto rounded-full bg-primary/15 px-1.5 py-0.5 font-medium text-[10px] text-primary">
            {active}
          </span>
        ) : null}
      </Button>

      {/* A named <aside>, not role="dialog": nothing here is modal -- no focus
          trap, no Esc-to-close, and the page stays interactive behind it,
          which is the point when the thing you're watching is a render. The
          aria-label also keeps it mapped to `complementary` rather than
          `generic`, since it sits inside the rail's <nav>. */}
      {open ? (
        <aside
          className="fixed inset-y-0 right-0 z-50 flex w-[340px] flex-col border-border border-l bg-background shadow-lg"
          aria-label="Activity"
        >
          <div className="flex items-center justify-between border-border border-b px-3 py-2.5">
            <span className="font-medium text-sm">Activity</span>
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Close
            </Button>
          </div>
          <ul className="min-h-0 flex-1 divide-y divide-border overflow-y-auto">
            {newest.map((record) => (
              <Row key={record.key} record={record} />
            ))}
          </ul>
          <p className="border-border border-t px-3 py-2 text-muted-foreground text-xs">
            Cleared when the page reloads — the API forgets jobs on restart too.
          </p>
        </aside>
      ) : null}
    </>
  );
}

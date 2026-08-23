import { useRouterState } from "@tanstack/react-router";
import { useCallback, useRef } from "react";

import { isTerminal, useJobs } from "@/hooks/useJobs";

export interface JobRunnerState {
  jobId: string | null;
  /** Human-readable one-liner, e.g. `Job abc123: running`. */
  status: string | null;
  error: string | null;
  /** Set once the job reports `done` -- the URL its preview element points at. */
  fileUrl: string | null;
  canSave: boolean;
  isRunning: boolean;
}

/** The one implementation of "submit, then poll until done" -- every
 * `/render/*`, `/generate/*` and `/assemble` route returns the same `{job_id}`
 * shape and is checked through the same `GET /jobs/{id}` contract, so all six
 * job-producing tabs share this rather than repeating a polling loop each.
 *
 * The loop itself now lives in `JobsProvider`, above the router, so a job
 * outlives the tab that started it (see that module for why). What's left here
 * is the per-tab view of it: this hook's shape is unchanged, which is why the
 * six tabs and `JobResult` needed no edits.
 *
 * `run` takes the submit call itself, so each tab keeps its own typed SDK call
 * and body construction and only the polling is shared.
 *
 * The owning route is read from the router rather than passed in by each tab:
 * the router already knows it, a hand-written copy would be one more thing to
 * keep in step with `src/routes/`, and `lib/tabs.ts` can turn it straight into
 * the label the activity drawer shows. Captured on first render, since the
 * pathname changes as soon as the user navigates away and the job's owner
 * must not change with it. */
export function useJobRunner() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const source = useRef(pathname).current;
  const jobs = useJobs();

  const record = jobs.currentFor(source);
  const failure = jobs.failureFor(source);

  const run = useCallback(
    (submit: () => Promise<string>) => jobs.start(source, submit),
    [jobs, source],
  );

  /** Client-side validation failures land in the same place a job error would,
   * so a tab has exactly one error surface (the old UI's `buildForm()` returning
   * null after writing to `errorEl` did the same thing). */
  const fail = useCallback((message: string) => jobs.fail(source, message), [jobs, source]);

  const status = record ? (record.id ? `Job ${record.id}: ${record.phase}` : "Submitting…") : null;

  return {
    jobId: record?.id ?? null,
    status,
    error: failure ?? record?.error ?? null,
    fileUrl: record?.fileUrl ?? null,
    canSave: record?.canSave ?? false,
    isRunning: !!record && !isTerminal(record.phase),
    run,
    fail,
  };
}

export type JobRunner = ReturnType<typeof useJobRunner>;

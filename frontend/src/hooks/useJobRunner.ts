import { useCallback, useEffect, useRef, useState } from "react";

import { DefaultService } from "@/client";
import { errorMessage, jobFileUrl } from "@/lib/api";

const POLL_INTERVAL_MS = 1500;

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

const IDLE: JobRunnerState = {
  jobId: null,
  status: null,
  error: null,
  fileUrl: null,
  canSave: false,
  isRunning: false,
};

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** The one implementation of "submit, then poll until done" -- every
 * `/render/*`, `/generate/*` and `/assemble` route returns the same `{job_id}`
 * shape and is checked through the same `GET /jobs/{id}` contract, so all six
 * job-producing tabs share this rather than repeating a polling loop each.
 *
 * Written as a plain async loop rather than a react-query `refetchInterval`
 * because the lifecycle here is a one-shot submit-then-watch, not a cache
 * entry: there's nothing to share between components, nothing to refetch on
 * remount, and the terminal states (`done`/`error`) are read once and kept.
 *
 * `run` takes the submit call itself, so each tab keeps its own typed SDK call
 * and body construction and only the polling is shared. */
export function useJobRunner() {
  const [state, setState] = useState<JobRunnerState>(IDLE);
  const cancelled = useRef(false);

  useEffect(() => {
    // Reset on mount, not just on unmount: under StrictMode React mounts,
    // unmounts and remounts the same instance (refs included), so a cleanup-only
    // version of this would leave `cancelled` stuck true for the real mount and
    // silently swallow every job update in development.
    cancelled.current = false;
    return () => {
      cancelled.current = true;
    };
  }, []);

  const run = useCallback(async (submit: () => Promise<string>) => {
    setState({ ...IDLE, isRunning: true });
    try {
      const jobId = await submit();
      if (cancelled.current) return;
      setState((prev) => ({ ...prev, jobId, status: `Job ${jobId}: submitted` }));

      for (;;) {
        const { data: job } = await DefaultService.jobStatusJobsJobIdGet({
          path: { job_id: jobId },
        });
        if (cancelled.current) return;
        setState((prev) => ({ ...prev, status: `Job ${jobId}: ${job.status}` }));

        if (job.status === "done") {
          setState((prev) => ({
            ...prev,
            isRunning: false,
            fileUrl: jobFileUrl(jobId),
            canSave: !!job.can_save,
          }));
          return;
        }
        if (job.status === "error") {
          setState((prev) => ({
            ...prev,
            isRunning: false,
            error: job.error ?? "Job failed without an error message.",
          }));
          return;
        }
        await delay(POLL_INTERVAL_MS);
      }
    } catch (err) {
      if (cancelled.current) return;
      setState((prev) => ({ ...prev, isRunning: false, error: errorMessage(err) }));
    }
  }, []);

  /** Client-side validation failures land in the same place a job error would,
   * so a tab has exactly one error surface (the old UI's `buildForm()` returning
   * null after writing to `errorEl` did the same thing). */
  const fail = useCallback((message: string) => {
    setState({ ...IDLE, error: message });
  }, []);

  return { ...state, run, fail };
}

export type JobRunner = ReturnType<typeof useJobRunner>;

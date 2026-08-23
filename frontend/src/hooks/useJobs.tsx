import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { JobsService } from "@/client";
import { errorMessage, jobFileUrl } from "@/lib/api";

const POLL_INTERVAL_MS = 1500;
/** Enough to see what just happened without the drawer becoming a log. */
const MAX_RECORDS = 20;

export type JobPhase = "submitting" | "pending" | "running" | "done" | "error";

export interface JobRecord {
  /** Client-side identity, assigned before the server has replied -- a job is
   * already "submitting" (and already worth showing) before it has an id. */
  key: string;
  id: string | null;
  /** Route path that started it, e.g. "/ui/music". Used to hand the record
   * back to the right tab and to label it in the drawer. */
  source: string;
  phase: JobPhase;
  error: string | null;
  fileUrl: string | null;
  canSave: boolean;
  startedAt: number;
  finishedAt: number | null;
}

export const isTerminal = (phase: JobPhase) => phase === "done" || phase === "error";

interface JobsContextValue {
  records: JobRecord[];
  /** The record a tab should currently be showing, or null when the tab was
   * reset by a validation failure. */
  currentFor: (source: string) => JobRecord | null;
  failureFor: (source: string) => string | null;
  start: (source: string, submit: () => Promise<string>) => Promise<void>;
  fail: (source: string, message: string) => void;
}

const JobsContext = createContext<JobsContextValue | null>(null);

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Holds every submitted job for the lifetime of the app, and runs the polling
 * loops itself.
 *
 * This used to live in `useJobRunner`, in the state of whichever tab submitted
 * the job -- which meant navigating away unmounted the component, flipped its
 * `cancelled` ref, and abandoned the poll. The render itself carried on
 * server-side and its file stayed downloadable, but the UI had thrown away the
 * job id, so the result was unreachable without knowing to look in the library.
 * For renders measured in minutes, "don't touch the sidebar" isn't a
 * reasonable thing to ask.
 *
 * Deliberately in memory only, not localStorage: server-side job state is an
 * in-process dict (see server/jobs.py) that a restart clears, so persisted ids
 * would come back as confident-looking rows pointing at 404s. Both sides
 * forget together. */
export function JobsProvider({ children }: { children: ReactNode }) {
  const [records, setRecords] = useState<JobRecord[]>([]);
  // source -> record key currently owned by that tab, or null once a
  // validation failure has reset it.
  const [current, setCurrent] = useState<Record<string, string | null>>({});
  const [failures, setFailures] = useState<Record<string, string | null>>({});
  // Only ever set on teardown of the whole app, unlike the per-component ref
  // this replaced -- a route change no longer cancels anything.
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;
    return () => {
      stopped.current = true;
    };
  }, []);

  const patch = useCallback((key: string, changes: Partial<JobRecord>) => {
    setRecords((prev) =>
      prev.map((record) => (record.key === key ? { ...record, ...changes } : record)),
    );
  }, []);

  const start = useCallback(
    async (source: string, submit: () => Promise<string>) => {
      const key = crypto.randomUUID();
      setFailures((prev) => ({ ...prev, [source]: null }));
      setRecords((prev) =>
        [
          ...prev,
          {
            key,
            id: null,
            source,
            phase: "submitting" as JobPhase,
            error: null,
            fileUrl: null,
            canSave: false,
            startedAt: Date.now(),
            finishedAt: null,
          },
        ].slice(-MAX_RECORDS),
      );
      setCurrent((prev) => ({ ...prev, [source]: key }));

      try {
        const jobId = await submit();
        if (stopped.current) return;
        patch(key, { id: jobId, phase: "pending" });

        for (;;) {
          const { data: job } = await JobsService.jobStatusJobsJobIdGet({
            path: { job_id: jobId },
          });
          if (stopped.current) return;

          if (job.status === "done") {
            patch(key, {
              phase: "done",
              fileUrl: jobFileUrl(jobId),
              canSave: !!job.can_save,
              finishedAt: Date.now(),
            });
            return;
          }
          if (job.status === "error") {
            patch(key, {
              phase: "error",
              error: job.error ?? "Job failed without an error message.",
              finishedAt: Date.now(),
            });
            return;
          }
          patch(key, { phase: job.status === "running" ? "running" : "pending" });
          await delay(POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (stopped.current) return;
        patch(key, { phase: "error", error: errorMessage(err), finishedAt: Date.now() });
      }
    },
    [patch],
  );

  /** Client-side validation failures aren't jobs, so they create no record --
   * they just clear whatever the tab was showing, the way resetting to an
   * empty state used to. */
  const fail = useCallback((source: string, message: string) => {
    setCurrent((prev) => ({ ...prev, [source]: null }));
    setFailures((prev) => ({ ...prev, [source]: message }));
  }, []);

  const currentFor = useCallback(
    (source: string) => {
      const key = current[source];
      if (!key) return null;
      return records.find((record) => record.key === key) ?? null;
    },
    [current, records],
  );

  const failureFor = useCallback((source: string) => failures[source] ?? null, [failures]);

  return (
    <JobsContext.Provider value={{ records, currentFor, failureFor, start, fail }}>
      {children}
    </JobsContext.Provider>
  );
}

export function useJobs() {
  const value = useContext(JobsContext);
  if (!value) throw new Error("useJobs must be used inside a JobsProvider");
  return value;
}

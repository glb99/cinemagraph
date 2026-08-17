import axios from "axios";

/** FastAPI's error body is `{"detail": ...}` -- a plain string for the
 * `HTTPException`s this API raises by hand, or pydantic's list of
 * `{loc, msg, type}` objects for a 422. The old single-page UI only ever read
 * `detail || "HTTP <status>"`, which rendered a 422 as a useless "[object
 * Object]"; both shapes are unpacked here instead.
 *
 * The generated client is configured with `throwOnError: true`, so every
 * non-2xx surfaces as a thrown AxiosError rather than a returned error union
 * -- this is the one place that has to know that. */
export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          const { loc, msg } = item as { loc?: unknown[]; msg?: string };
          const where = Array.isArray(loc) ? loc.join(".") : "";
          return where ? `${where}: ${msg}` : (msg ?? String(item));
        })
        .join("; ");
    }
    return err.response ? `HTTP ${err.response.status}` : err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

/** Media URLs are used as `src` attributes on <video>/<audio>/<img>, so they're
 * built as plain paths rather than fetched through the generated SDK -- the
 * browser does the request itself, and both routes stream bytes rather than
 * JSON. Same origin as the app, so Vite's dev proxy (and the reverse proxy in
 * any real deployment) routes them to the API unchanged. */
export const jobFileUrl = (jobId: string) => `/jobs/${encodeURIComponent(jobId)}/file`;
export const libraryFileUrl = (assetId: string) => `/library/${encodeURIComponent(assetId)}/file`;

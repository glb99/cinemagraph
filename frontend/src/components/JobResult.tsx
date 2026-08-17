import { SaveToLibrary } from "@/components/SaveToLibrary";
import { ErrorText } from "@/components/ui/field";
import type { JobRunner } from "@/hooks/useJobRunner";

/** The tail end of every job-producing tab: the status line, any error, the
 * finished result, and (when the server staged a save candidate) the
 * save-to-library control. Which media element to use is per-tab knowledge --
 * a photo render is a video, a music generation is audio -- so it's a prop
 * rather than something inferred here. */
export function JobResult({
  runner,
  preview,
}: {
  runner: JobRunner;
  preview: "video" | "audio" | "image";
}) {
  return (
    <div className="space-y-3">
      {runner.status && (
        <p className="text-sm text-muted-foreground" data-testid="job-status">
          {runner.status}
        </p>
      )}
      <ErrorText>{runner.error}</ErrorText>

      {runner.fileUrl && (
        <div data-testid="job-preview">
          {preview === "video" && (
            // biome-ignore lint/a11y/useMediaCaption: a rendered cinemagraph has no audio track to caption.
            <video src={runner.fileUrl} controls loop className="max-w-full rounded-lg" />
          )}
          {preview === "audio" && (
            // biome-ignore lint/a11y/useMediaCaption: generated audio has no caption track.
            <audio src={runner.fileUrl} controls className="w-full" />
          )}
          {preview === "image" && (
            <img src={runner.fileUrl} alt="Generated result" className="max-w-full rounded-lg" />
          )}
        </div>
      )}

      {runner.canSave && runner.jobId && <SaveToLibrary key={runner.jobId} jobId={runner.jobId} />}
    </div>
  );
}

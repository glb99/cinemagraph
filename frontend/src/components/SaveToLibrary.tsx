import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { DefaultService } from "@/client";
import { NEW_PROJECT_VALUE, ProjectAssignSelect, resolveProject } from "@/components/ProjectSelect";
import { Button } from "@/components/ui/button";
import { ErrorText } from "@/components/ui/field";
import { errorMessage } from "@/lib/api";

/** Nothing is auto-saved to the library: a finished job stages a save candidate
 * server-side (jobs.py's `pending_library`) and only POST /jobs/{id}/save
 * actually registers it, once the result has been seen or heard and judged
 * worth keeping. The project picker beside the button is the same
 * "decide at save time" moment for project assignment -- defaulting to
 * "No project" keeps the common case one click. */
export function SaveToLibrary({ jobId }: { jobId: string }) {
  const [project, setProject] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: async () => {
      const chosen = resolveProject(project, newProjectName);
      await DefaultService.saveJobJobsJobIdSavePost({
        path: { job_id: jobId },
        body: chosen ? { project: chosen } : {},
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["library"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <ProjectAssignSelect
          value={project}
          newName={newProjectName}
          onValueChange={setProject}
          onNewNameChange={setNewProjectName}
          disabled={save.isPending || save.isSuccess}
        />
        <Button
          type="button"
          variant="success"
          disabled={save.isPending || save.isSuccess}
          onClick={() => save.mutate()}
        >
          {save.isSuccess ? "✓ Saved" : save.isPending ? "Saving…" : "Save to library"}
        </Button>
        {project === NEW_PROJECT_VALUE && !newProjectName.trim() && !save.isSuccess && (
          <span className="text-xs text-muted-foreground">
            Leave the name blank to save without a project.
          </span>
        )}
      </div>
      <ErrorText>{save.isError ? errorMessage(save.error) : null}</ErrorText>
    </div>
  );
}

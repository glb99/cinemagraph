import { useState } from "react";

import type { AssetResponse } from "@/client";
import { AssetPreview } from "@/components/AssetPreview";
import { NEW_PROJECT_VALUE, ProjectAssignSelect, resolveProject } from "@/components/ProjectSelect";
import { Button } from "@/components/ui/button";
import { ErrorText } from "@/components/ui/field";
import { useDeleteAsset, useSetAssetProject } from "@/hooks/useLibrary";
import { errorMessage } from "@/lib/api";
import { assetPrompt } from "@/lib/assets";

/** `original_filename` and `tags` are user-supplied (an upload's own name, or
 * free-text tags from a manual `library add`). They're rendered as JSX text
 * nodes, which React escapes -- the old page had to reach for
 * createElement/textContent by hand to get the same guarantee. */
export function LibraryCard({ asset }: { asset: AssetResponse }) {
  const [project, setProject] = useState(asset.project ?? "");
  const [newProjectName, setNewProjectName] = useState("");
  const setAssetProject = useSetAssetProject();
  const deleteAsset = useDeleteAsset();

  // Assignment lands immediately: picking an existing project commits on
  // change, while "+ New project…" waits for Enter in its text input, since a
  // half-typed name shouldn't be written on every keystroke.
  const commitProject = (value: string, name: string) => {
    const chosen = resolveProject(value, name);
    if (value === NEW_PROJECT_VALUE && !chosen) return;
    setAssetProject.mutate({ assetId: asset.id, project: chosen });
  };

  const prompt = assetPrompt(asset);
  const error = setAssetProject.isError
    ? errorMessage(setAssetProject.error)
    : deleteAsset.isError
      ? errorMessage(deleteAsset.error)
      : null;

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-3">
      <AssetPreview asset={asset} />

      <div className="space-y-1 text-sm">
        <div>
          <strong>{asset.kind}</strong> · {asset.original_filename}
        </div>
        <div className="text-xs text-muted-foreground">
          {new Date(asset.added_at).toLocaleString()}
        </div>
        {asset.tags.length > 0 && (
          <div className="text-xs text-muted-foreground">tags: {asset.tags.join(", ")}</div>
        )}
        {/* The full prompt, not the truncated form the pickers show -- a card is
            the one place it's worth reading end to end. */}
        {prompt && <div className="text-xs text-muted-foreground">Prompt: {prompt}</div>}
      </div>

      <ProjectAssignSelect
        value={project}
        newName={newProjectName}
        aria-label={`Project for ${asset.original_filename}`}
        onValueChange={(value) => {
          setProject(value);
          if (value !== NEW_PROJECT_VALUE) commitProject(value, newProjectName);
        }}
        onNewNameChange={setNewProjectName}
        onNewNameSubmit={() => commitProject(project, newProjectName)}
        disabled={setAssetProject.isPending}
      />

      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={deleteAsset.isPending}
        onClick={() => {
          if (!window.confirm(`Delete "${asset.original_filename}"? This cannot be undone.`))
            return;
          deleteAsset.mutate(asset.id);
        }}
      >
        Delete
      </Button>

      <ErrorText>{error}</ErrorText>
    </div>
  );
}

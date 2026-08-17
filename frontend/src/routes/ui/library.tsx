import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";

import { LibraryCard } from "@/components/LibraryCard";
import { ProjectFilterSelect } from "@/components/ProjectSelect";
import { UploadToLibrary } from "@/components/UploadToLibrary";
import { Button } from "@/components/ui/button";
import { ErrorText, Hint, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useLibraryAssets } from "@/hooks/useLibrary";
import { useDeleteProject, useRenameProject } from "@/hooks/useProjects";
import { errorMessage } from "@/lib/api";

export const Route = createFileRoute("/ui/library")({
  component: LibraryTab,
});

const KINDS = ["reference", "source", "generated"];

function LibraryTab() {
  const [kind, setKind] = useState("");
  // Typed vs. applied: the tag filter is a server-side query param, so it's
  // committed on Enter (or Refresh) rather than refetching per keystroke.
  const [tagInput, setTagInput] = useState("");
  const [tag, setTag] = useState("");
  const [project, setProject] = useState("");

  const [manageProject, setManageProject] = useState("");
  const [renameTo, setRenameTo] = useState("");
  const [manageError, setManageError] = useState<string | null>(null);

  const {
    data: assets,
    isPending,
    isError,
    error,
    refetch,
  } = useLibraryAssets({
    kind,
    tag,
    project,
  });
  const renameProject = useRenameProject();
  const deleteProject = useDeleteProject();

  const submitRename = async () => {
    if (!manageProject || !renameTo.trim()) {
      setManageError("Pick a project and type a new name.");
      return;
    }
    setManageError(null);
    const to = renameTo.trim();
    try {
      await renameProject.mutateAsync({ from: manageProject, to });
    } catch (err) {
      setManageError(errorMessage(err));
      return;
    }
    setRenameTo("");
    // Repoint any filter that was pointing at the name that just changed --
    // otherwise the list would refetch under a name that no longer exists and
    // read as "No assets yet" even though the renamed project's assets are
    // right there.
    if (project === manageProject) setProject(to);
    setManageProject(to);
  };

  const submitDelete = async () => {
    if (!manageProject) {
      setManageError("Pick a project to delete.");
      return;
    }
    if (
      !window.confirm(
        `Remove project "${manageProject}" from all its assets? The assets themselves are kept.`,
      )
    ) {
      return;
    }
    setManageError(null);
    try {
      await deleteProject.mutateAsync(manageProject);
    } catch (err) {
      setManageError(errorMessage(err));
      return;
    }
    if (project === manageProject) setProject("");
    setManageProject("");
  };

  return (
    <div className="space-y-4">
      <UploadToLibrary />

      <div className="flex flex-wrap items-center gap-3">
        <InlineField label="Kind">
          <Select value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">all</option>
            {KINDS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </InlineField>
        <InlineField label="Tag">
          <Input
            type="text"
            placeholder="e.g. photo"
            className="w-40"
            value={tagInput}
            onChange={(event) => setTagInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              setTag(tagInput.trim());
            }}
          />
        </InlineField>
        <InlineField label="Project">
          <ProjectFilterSelect value={project} onChange={setProject} aria-label="Project filter" />
        </InlineField>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setTag(tagInput.trim());
            refetch();
          }}
        >
          Refresh
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <strong>Manage projects:</strong>
        <ProjectFilterSelect
          value={manageProject}
          onChange={setManageProject}
          placeholder="(pick a project)"
          aria-label="Project to manage"
        />
        <span className="text-muted-foreground text-xs">rename to</span>
        <Input
          type="text"
          placeholder="new name"
          className="w-40"
          aria-label="New project name"
          value={renameTo}
          onChange={(event) => setRenameTo(event.target.value)}
        />
        <Button type="button" variant="outline" size="sm" onClick={submitRename}>
          Rename
        </Button>
        <Button type="button" variant="destructive" size="sm" onClick={submitDelete}>
          Delete
        </Button>
      </div>

      <ErrorText>{manageError}</ErrorText>
      <ErrorText>{isError ? errorMessage(error) : null}</ErrorText>

      {isPending && <Hint>Loading…</Hint>}
      {assets && assets.length === 0 && <Hint>No assets yet.</Hint>}

      <div
        className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(220px,1fr))]"
        data-testid="library-grid"
      >
        {assets?.map((asset) => (
          <LibraryCard key={asset.id} asset={asset} />
        ))}
      </div>
    </div>
  );
}

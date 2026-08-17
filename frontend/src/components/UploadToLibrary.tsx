import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { DefaultService } from "@/client";
import { ProjectAssignSelect, resolveProject } from "@/components/ProjectSelect";
import { Button } from "@/components/ui/button";
import { ErrorText, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { errorMessage } from "@/lib/api";

const KINDS = ["reference", "source", "generated"];

/** Brings a local file (a song to remix against, footage to render, anything
 * else) into the library directly, rather than only ever picking one up as a
 * side effect of a render/generate job. Same POST /library the CLI's
 * `cinemagraph library add` and every job's auto-registration already use --
 * this is just the browser-side door into it. Defaults to kind="reference"
 * since that's what a hand-picked upload (as opposed to this app's own
 * output) almost always is. */
export function UploadToLibrary() {
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("reference");
  const [tags, setTags] = useState("");
  const [project, setProject] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a file first.");
      const chosenProject = resolveProject(project, newProjectName);
      await DefaultService.libraryAddLibraryPost({
        body: {
          upload: file,
          kind,
          tags,
          project: chosenProject || undefined,
        },
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["library"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      setFile(null);
      setTags("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
  });

  return (
    <div className="space-y-2 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          ref={fileInputRef}
          type="file"
          className="w-auto"
          aria-label="File to upload"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
        <InlineField label="Kind">
          <Select value={kind} onChange={(event) => setKind(event.target.value)}>
            {KINDS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </InlineField>
        <InlineField label="Tags">
          <Input
            type="text"
            placeholder="comma, separated"
            className="w-40"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
          />
        </InlineField>
        <ProjectAssignSelect
          value={project}
          newName={newProjectName}
          onValueChange={setProject}
          onNewNameChange={setNewProjectName}
          disabled={upload.isPending}
        />
        <Button type="button" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>
          {upload.isPending ? "Uploading…" : "Upload"}
        </Button>
        {upload.isSuccess && !upload.isPending && (
          <span className="text-xs text-success">✓ Uploaded</span>
        )}
      </div>
      <ErrorText>{upload.isError ? errorMessage(upload.error) : null}</ErrorText>
    </div>
  );
}

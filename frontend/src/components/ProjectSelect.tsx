import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useProjects } from "@/hooks/useProjects";
import { cn } from "@/lib/utils";

export const NEW_PROJECT_VALUE = "__new__";

/** "" (no project) unless "+ New project…" is picked, in which case the paired
 * text input's trimmed value is used. A blank new-project name resolves back to
 * "" -- callers treat that as "no project" either way, so it's a harmless no-op
 * rather than a validation error. */
export function resolveProject(value: string, newName: string): string {
  return value === NEW_PROJECT_VALUE ? newName.trim() : value;
}

export interface ProjectAssignSelectProps {
  value: string;
  newName: string;
  onValueChange: (value: string) => void;
  onNewNameChange: (name: string) => void;
  /** Enter pressed in the new-name input -- used where assignment happens
   * immediately (a library card) rather than at some later submit. */
  onNewNameSubmit?: () => void;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}

/** Assigns a project: every existing name, plus "No project" and a trailing
 * "+ New project…" that reveals a free-text input. Both places that assign one
 * (the save-to-library flow, a library card) use this; the project *filters*
 * are a different control -- see ProjectFilterSelect. */
export function ProjectAssignSelect({
  value,
  newName,
  onValueChange,
  onNewNameChange,
  onNewNameSubmit,
  disabled,
  className,
  "aria-label": ariaLabel = "Project",
}: ProjectAssignSelectProps) {
  const { data: projects } = useProjects();

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <Select
        aria-label={ariaLabel}
        value={value}
        disabled={disabled}
        onChange={(event) => onValueChange(event.target.value)}
      >
        <option value="">No project</option>
        {projects?.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
        <option value={NEW_PROJECT_VALUE}>+ New project…</option>
      </Select>
      {value === NEW_PROJECT_VALUE && (
        <Input
          className="w-44"
          placeholder="new project name"
          aria-label="New project name"
          value={newName}
          disabled={disabled}
          onChange={(event) => onNewNameChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || !onNewNameSubmit) return;
            event.preventDefault();
            onNewNameSubmit();
          }}
        />
      )}
    </div>
  );
}

export interface ProjectFilterSelectProps {
  value: string;
  onChange: (value: string) => void;
  /** Label for the "no filter" entry -- "all" for a filter, "(pick a project)"
   * for the manage dropdown. */
  placeholder?: string;
  "aria-label"?: string;
}

/** Picks among *existing* projects (Library's filter and manage dropdowns,
 * Assemble's filter) -- no "No project"/"+ New project…" entries, since nothing
 * here assigns a project. */
export function ProjectFilterSelect({
  value,
  onChange,
  placeholder = "all",
  "aria-label": ariaLabel = "Project",
}: ProjectFilterSelectProps) {
  const { data: projects } = useProjects();

  return (
    <Select aria-label={ariaLabel} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {projects?.map((name) => (
        <option key={name} value={name}>
          {name}
        </option>
      ))}
    </Select>
  );
}

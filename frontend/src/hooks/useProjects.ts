import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DefaultService } from "@/client";

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: async () => (await DefaultService.listProjectsProjectsGet()).data,
  });
}

/** Renaming or deleting a project rewrites the `project` field on every asset
 * carrying it, so the library lists are invalidated alongside the project list
 * itself. */
function useProjectInvalidator() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["projects"] });
    queryClient.invalidateQueries({ queryKey: ["library"] });
  };
}

export function useRenameProject() {
  const invalidate = useProjectInvalidator();
  return useMutation({
    mutationFn: async ({ from, to }: { from: string; to: string }) =>
      DefaultService.renameProjectProjectsRenamePost({ body: { old: from, new: to } }),
    onSuccess: invalidate,
  });
}

export function useDeleteProject() {
  const invalidate = useProjectInvalidator();
  return useMutation({
    mutationFn: async (name: string) =>
      DefaultService.deleteProjectProjectsNameDelete({ path: { name } }),
    onSuccess: invalidate,
  });
}

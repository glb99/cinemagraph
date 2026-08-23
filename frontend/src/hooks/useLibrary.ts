import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { LibraryService } from "@/client";

export interface LibraryFilters {
  kind?: string;
  tag?: string;
  project?: string;
}

/** Blank filters are dropped rather than sent as empty strings, so the query
 * key for "no filters" is stable no matter which control was cleared last. */
function libraryQuery(filters: LibraryFilters) {
  const query: LibraryFilters = {};
  if (filters.kind) query.kind = filters.kind;
  if (filters.tag) query.tag = filters.tag;
  if (filters.project) query.project = filters.project;
  return query;
}

export function useLibraryAssets(filters: LibraryFilters = {}) {
  const query = libraryQuery(filters);
  return useQuery({
    queryKey: ["library", query] as const,
    queryFn: async () => (await LibraryService.libraryListLibraryGet({ query })).data,
  });
}

/** Every mutation below invalidates the whole "library" key space rather than
 * one filtered list: an asset's project/deletion changes which filtered lists
 * it belongs to, so patching just the list it was fetched from would leave the
 * others stale. */
function useLibraryInvalidator() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["library"] });
    queryClient.invalidateQueries({ queryKey: ["projects"] });
  };
}

export function useDeleteAsset() {
  const invalidate = useLibraryInvalidator();
  return useMutation({
    mutationFn: async (assetId: string) =>
      LibraryService.libraryRemoveLibraryAssetIdDelete({ path: { asset_id: assetId } }),
    onSuccess: invalidate,
  });
}

export function useSetAssetProject() {
  const invalidate = useLibraryInvalidator();
  return useMutation({
    mutationFn: async ({ assetId, project }: { assetId: string; project: string }) =>
      LibraryService.setAssetProjectLibraryAssetIdProjectPost({
        path: { asset_id: assetId },
        body: { project },
      }),
    onSuccess: invalidate,
  });
}

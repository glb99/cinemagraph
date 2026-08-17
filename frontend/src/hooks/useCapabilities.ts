import { useQuery } from "@tanstack/react-query";

import { DefaultService } from "@/client";

/** GET /capabilities is checked fresh per request server-side (each optional
 * satellite is health-checked when asked), so this is cached for the session
 * rather than refetched on every tab switch -- a satellite that comes up after
 * the page loaded is picked up on the next reload, same as the old UI, which
 * fetched this exactly once at startup. */
export function useCapabilities() {
  return useQuery({
    queryKey: ["capabilities"],
    queryFn: async () => (await DefaultService.capabilitiesCapabilitiesGet()).data,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** The effect registry is static for the life of the server process. */
export function useEffects() {
  return useQuery({
    queryKey: ["effects"],
    queryFn: async () => {
      const { data } = await DefaultService.listEffectsEffectsGet();
      // GET /effects has no `response_model`, so the generated type for it is
      // `unknown` -- narrowed here rather than pretending the codegen knows a
      // shape it was never told about.
      return (data as { effects: string[] }).effects;
    },
    staleTime: Number.POSITIVE_INFINITY,
  });
}

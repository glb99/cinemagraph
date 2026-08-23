import type { CapabilitiesResponse } from "@/client";

/** Static per-adapter facts the API doesn't carry -- where it runs and what it
 * costs, keyed by the same model id `model=` accepts on /generate/image and
 * /generate/music (see server/app.py's registration block). Small enough to
 * keep alongside SERVICE_START_COMMANDS in lib/tabs.ts rather than asking the
 * backend to describe its own pricing.
 *
 * Local adapters (sdxl/acestep) are registered unconditionally at import time
 * -- they always appear in `image_generation_models`/`music_generation_models`
 * whether or not the satellite container is actually reachable right now, so
 * "known" and "ready" are different questions for them. Hosted adapters
 * (gemini/lyria3) only register when GEMINI_API_KEY is set, so for them,
 * simply not being in that list already means "needs a key". */
export type EngineWhere = "local" | "hosted";

export interface EngineDef {
  id: string;
  label: string;
  where: EngineWhere;
  cost: string;
  /** The service env var this local engine runs behind -- undefined for
   * hosted engines, which have no satellite to be reachable or not. */
  envVar?: string;
}

export const IMAGE_ENGINES: EngineDef[] = [
  {
    id: "sdxl",
    label: "SDXL",
    where: "local",
    cost: "Free · a GPU render, ~30s · holds ~7 GB of VRAM",
    envVar: "IMAGE_GENERATION_URL",
  },
  {
    id: "gemini",
    label: "Gemini",
    where: "hosted",
    cost: "No GPU · billed to your Google AI key",
  },
];

export const MUSIC_ENGINES: EngineDef[] = [
  {
    id: "acestep",
    label: "ACE-Step",
    where: "local",
    cost: "Free · a few minutes · holds ~8 GB of VRAM",
    envVar: "ACESTEP_URL",
  },
  {
    id: "lyria3",
    label: "Lyria 3",
    where: "hosted",
    cost: "No GPU · billed to your Google AI key",
  },
];

export type EngineState = "ready" | "down" | "off";

/** `registered` is the live `*_generation_models` list -- membership is the
 * only signal for hosted engines (no health check to ask), and the starting
 * point for local ones before checking whether the satellite actually
 * answers right now. */
export function engineState(
  engine: EngineDef,
  capabilities: CapabilitiesResponse | undefined,
  registered: string[],
): EngineState {
  if (!registered.includes(engine.id)) return "off";
  if (engine.envVar) {
    const service = capabilities?.services?.find(
      (candidate) => candidate.env_var === engine.envVar,
    );
    if (service && !service.reachable) return service.configured ? "down" : "off";
  }
  return "ready";
}

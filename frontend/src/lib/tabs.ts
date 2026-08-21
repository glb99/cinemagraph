import type { CapabilitiesResponse } from "@/client";

/** Every tab route is namespaced under /ui, and "/" redirects there.
 *
 * That prefix isn't decoration: `server.app:app` serves its routes unprefixed
 * (no shared "/api" base -- see vite.config.ts), so it already owns top-level
 * paths including `/library` and `/assemble`. A client-side route on either of
 * those names is indistinguishable from the API path it shadows, which is not a
 * theoretical clash -- `/library` really did resolve to the JSON endpoint
 * instead of the app the first time these routes were laid out flat. Filtering
 * on the request's `Accept` header would separate navigations from the SDK's
 * own calls, but it would also break the plain download links the library falls
 * back to for non-previewable assets, which are navigations to an API path.
 * Namespacing the app instead leaves the API's URL space untouched.
 *
 * One tab per route. Photo/Video/Assemble/Library are always available;
 * Music/Sound effects/Image are feature-gated on GET /capabilities -- the same
 * signal the rest of the system uses to degrade gracefully when an optional
 * service isn't running.
 *
 * Three states, matching the old single-page UI:
 *   - available            -> normal link
 *   - configured, not up   -> link with a ⚠ marker, plus a hint banner inside
 *                             the route telling you which container to start
 *   - not configured       -> no link at all (nothing to hint about without
 *                             redeploying)
 * See app.py's /capabilities docstring for the configured/available split. */
/** How to start each optional satellite, keyed by the env var that configures
 * it -- the same key `GET /capabilities`'s `services[]` reports.
 *
 * Keyed by env var rather than by tab because the two don't line up: semantic
 * masking has no tab of its own (it's a field inside the Photo tab), so it
 * would otherwise have no home here, and the Setup tab needs a command for all
 * four. The TABS entries below reference this rather than repeating it. */
export const SERVICE_START_COMMANDS: Record<string, string> = {
  ML_SERVICE_URL: "docker compose --profile ml up machine-learning",
  ACESTEP_URL: "docker compose --profile audio up acestep",
  SOUND_EFFECTS_URL: "docker compose --profile audio up sound-effects",
  IMAGE_GENERATION_URL: "docker compose --profile image up image-generation",
};

export interface TabDef {
  to:
    | "/ui"
    | "/ui/video"
    | "/ui/music"
    | "/ui/sfx"
    | "/ui/image"
    | "/ui/assemble"
    | "/ui/library"
    | "/ui/setup";
  label: string;
  always?: boolean;
  capability?: keyof Pick<
    CapabilitiesResponse,
    "music_generation" | "sound_effect_generation" | "image_generation"
  >;
  startCommand?: string;
}

export const TABS: TabDef[] = [
  { to: "/ui", label: "Photo", always: true },
  { to: "/ui/video", label: "Video", always: true },
  {
    to: "/ui/music",
    label: "Music",
    capability: "music_generation",
    startCommand: SERVICE_START_COMMANDS.ACESTEP_URL,
  },
  {
    to: "/ui/sfx",
    label: "Sound effects",
    capability: "sound_effect_generation",
    startCommand: SERVICE_START_COMMANDS.SOUND_EFFECTS_URL,
  },
  {
    to: "/ui/image",
    label: "Image",
    capability: "image_generation",
    startCommand: SERVICE_START_COMMANDS.IMAGE_GENERATION_URL,
  },
  { to: "/ui/assemble", label: "Assemble", always: true },
  { to: "/ui/library", label: "Library", always: true },
  { to: "/ui/setup", label: "Setup", always: true },
];

export interface TabAvailability {
  available: boolean;
  configured: boolean;
}

export function tabAvailability(
  tab: TabDef,
  capabilities: CapabilitiesResponse | undefined,
): TabAvailability {
  if (tab.always || !tab.capability) return { available: true, configured: true };
  return {
    available: !!capabilities?.[tab.capability],
    configured: !!capabilities?.configured?.[tab.capability],
  };
}

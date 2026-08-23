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
 * **Nothing here is ever hidden.** Every feature this tool has is listed
 * whatever the environment happens to be running, and availability shows up as
 * a status dot beside the row instead of as the row's absence. The previous
 * design removed unconfigured tabs entirely, which meant the navigation changed
 * shape depending on which containers were up -- so you could not discover that
 * a feature existed, and a tab you used yesterday could silently vanish today.
 * Three states, all of them visible:
 *   - available          -> normal link, green dot
 *   - configured, not up -> normal link, amber dot, hint banner inside the route
 *   - not configured     -> normal link, grey dot; the route explains what it
 *                           needs and how to switch it on
 *
 * Route paths are deliberately unchanged from the labels below (`/ui/assemble`
 * is "Timeline", `/ui/setup` is "Settings"), so nothing anyone has bookmarked
 * or linked breaks over a rename.
 * See app.py's /capabilities docstring for the configured/available split. */

/** How to start each optional satellite, keyed by the env var that configures
 * it -- the same key `GET /capabilities`'s `services[]` reports.
 *
 * Keyed by env var rather than by tab because the two don't line up: semantic
 * masking has no tab of its own (it's a field inside the Photo tab), so it
 * would otherwise have no home here, and the Settings tab needs a command for
 * all four. The TABS entries below reference this rather than repeating it. */
export const SERVICE_START_COMMANDS: Record<string, string> = {
  ML_SERVICE_URL: "docker compose --profile ml up machine-learning",
  ACESTEP_URL: "docker compose --profile audio up acestep",
  SOUND_EFFECTS_URL: "docker compose --profile audio up sound-effects",
  IMAGE_GENERATION_URL: "docker compose --profile image up image-generation",
};

/** Rail sections. "system" sits apart at the bottom -- it's where you go to
 * change the app rather than to make something with it. */
export type TabGroup = "create" | "assemble" | "system";

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
  group: TabGroup;
  /** Lucide-style icon key; see components/NavIcon.tsx. */
  icon: string;
  always?: boolean;
  capability?: keyof Pick<
    CapabilitiesResponse,
    "music_generation" | "sound_effect_generation" | "image_generation"
  >;
  startCommand?: string;
}

export const TABS: TabDef[] = [
  { to: "/ui", label: "From photo", group: "create", icon: "photo", always: true },
  { to: "/ui/video", label: "From video", group: "create", icon: "video", always: true },
  {
    to: "/ui/image",
    label: "Generate image",
    group: "create",
    icon: "sparkle",
    capability: "image_generation",
    startCommand: SERVICE_START_COMMANDS.IMAGE_GENERATION_URL,
  },
  {
    to: "/ui/music",
    label: "Music",
    group: "create",
    icon: "music",
    capability: "music_generation",
    startCommand: SERVICE_START_COMMANDS.ACESTEP_URL,
  },
  {
    to: "/ui/sfx",
    label: "Sound effects",
    group: "create",
    icon: "waveform",
    capability: "sound_effect_generation",
    startCommand: SERVICE_START_COMMANDS.SOUND_EFFECTS_URL,
  },
  { to: "/ui/assemble", label: "Timeline", group: "assemble", icon: "layers", always: true },
  { to: "/ui/library", label: "Library", group: "assemble", icon: "grid", always: true },
  { to: "/ui/setup", label: "Settings", group: "system", icon: "settings", always: true },
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

export type TabStatus = "ready" | "down" | "off" | "none";

/** What the dot beside a rail row means. `none` is for rows with no engine
 * behind them at all (photo, video, library) -- they get no dot, because a
 * permanent green dot on something that can never be off is noise. */
export function tabStatus(tab: TabDef, capabilities: CapabilitiesResponse | undefined): TabStatus {
  if (!tab.capability) return "none";
  const { available, configured } = tabAvailability(tab, capabilities);
  if (available) return "ready";
  return configured ? "down" : "off";
}

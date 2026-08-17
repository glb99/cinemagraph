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
export interface TabDef {
  to: "/ui" | "/ui/video" | "/ui/music" | "/ui/sfx" | "/ui/image" | "/ui/assemble" | "/ui/library";
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
    startCommand: "docker compose --profile audio up acestep",
  },
  {
    to: "/ui/sfx",
    label: "Sound effects",
    capability: "sound_effect_generation",
    startCommand: "docker compose --profile audio up sound-effects",
  },
  {
    to: "/ui/image",
    label: "Image",
    capability: "image_generation",
    startCommand: "docker compose --profile image up image-generation",
  },
  { to: "/ui/assemble", label: "Assemble", always: true },
  { to: "/ui/library", label: "Library", always: true },
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

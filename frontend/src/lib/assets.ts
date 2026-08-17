import type { AssetResponse } from "@/client";

/** How to preview an asset -- picked by file extension, not by `kind`, since
 * `kind` alone doesn't disambiguate (a "generated" cinemagraph and a
 * "generated" sound effect are both `kind="generated"` but need <video> and
 * <audio> respectively). */
export type AssetMediaKind = "image" | "video" | "audio" | "file";

const IMAGE_EXTS = ["jpg", "jpeg", "png", "webp", "gif"];
const VIDEO_EXTS = ["mp4", "webm", "mov"];
const AUDIO_EXTS = ["mp3", "wav", "ogg"];

/** Photo-render inputs: no .gif (the render reads a single still) even though
 * the preview classifier above happily displays one. */
export const PHOTO_INPUT_EXTS = ["jpg", "jpeg", "png", "webp"];

/** Remix inputs accept more than the three formats with an <audio> preview --
 * a .flac/.m4a in the library is still a valid song to cover or repaint, it
 * just falls back to a download link in its picker row. */
export const REMIX_AUDIO_EXTS = [...AUDIO_EXTS, "flac", "m4a"];

export function assetExtension(asset: AssetResponse): string {
  return (asset.original_filename.split(".").pop() ?? "").toLowerCase();
}

export function assetMediaKind(asset: AssetResponse): AssetMediaKind {
  const ext = assetExtension(asset);
  if (IMAGE_EXTS.includes(ext)) return "image";
  if (VIDEO_EXTS.includes(ext)) return "video";
  if (AUDIO_EXTS.includes(ext)) return "audio";
  return "file";
}

export function assetHasExtension(asset: AssetResponse, exts: string[]): boolean {
  return exts.includes(assetExtension(asset));
}

/** `provenance` is an untyped JSON blob on the wire (`dict[str, Any]`
 * server-side), so its fields are narrowed here rather than trusted. */
export function assetPrompt(asset: AssetResponse): string | null {
  const provenance = asset.provenance as { prompt?: unknown; mask_prompt?: unknown } | null;
  const prompt = provenance?.prompt ?? provenance?.mask_prompt;
  return typeof prompt === "string" && prompt ? prompt : null;
}

/** Every generated asset's `original_filename` is literally "output.mp4"/
 * "output.mp3"/"output.png" -- the fixed name every job writes before it's
 * hashed into the library -- so it's identical across generated assets and
 * useless as a picker label on its own. Prefers what the user actually typed
 * (`provenance.prompt`) when present; falls back to filename + when it was
 * added, since non-generated assets still need *something* to tell two
 * same-named entries apart. */
export function assetPickerLabel(asset: AssetResponse): string {
  const shortId = asset.id.slice(0, 8);
  const prompt = assetPrompt(asset);
  if (prompt) {
    const trimmed = prompt.length > 40 ? `${prompt.slice(0, 40)}…` : prompt;
    return `${trimmed} (${shortId})`;
  }
  const when = new Date(asset.added_at).toLocaleString();
  return `${asset.original_filename} — ${when} (${shortId})`;
}

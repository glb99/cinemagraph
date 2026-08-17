import type { AssetResponse } from "@/client";
import { libraryFileUrl } from "@/lib/api";
import { assetMediaKind } from "@/lib/assets";
import { cn } from "@/lib/utils";

export interface AssetPreviewProps {
  asset: AssetResponse;
  /** `card` is a full preview with playback controls; `row` is the thumbnail
   * used inside a picker row, where the point is identifying a candidate
   * rather than watching it end to end. */
  variant?: "card" | "row";
  className?: string;
}

export function AssetPreview({ asset, variant = "card", className }: AssetPreviewProps) {
  const url = libraryFileUrl(asset.id);
  const kind = assetMediaKind(asset);
  const isRow = variant === "row";

  if (kind === "image") {
    return (
      <img
        src={url}
        alt={asset.original_filename}
        className={cn(
          "rounded-md object-cover",
          isRow ? "size-14 shrink-0" : "max-w-full",
          className,
        )}
      />
    );
  }

  if (kind === "video") {
    // Native controls at thumbnail size are too cramped to be useful, so a
    // picker row plays silently on a loop instead: for a *cinemagraph* tool the
    // motion itself is the identifying signal, more than any single frame is.
    return (
      <video
        src={url}
        controls={!isRow}
        loop
        muted={isRow}
        autoPlay={isRow}
        playsInline
        className={cn(
          "rounded-md",
          isRow ? "h-14 w-24 shrink-0 object-cover" : "max-w-full",
          className,
        )}
      />
    );
  }

  if (kind === "audio") {
    return (
      // biome-ignore lint/a11y/useMediaCaption: generated audio has no caption track.
      <audio
        src={url}
        controls
        className={cn(isRow ? "h-8 min-w-0 flex-1" : "w-full", className)}
      />
    );
  }

  return (
    <a href={url} className={cn("text-sm underline", className)}>
      Download
    </a>
  );
}

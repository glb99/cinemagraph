import type * as React from "react";

import type { AssetResponse } from "@/client";
import { AssetPreview } from "@/components/AssetPreview";
import { Chip, ChipRow } from "@/components/Chip";
import { Button } from "@/components/ui/button";
import { assetPickerLabel } from "@/lib/assets";
import { cn } from "@/lib/utils";

/** The scrolling box every picker sits in. Capped in height so a library with
 * hundreds of assets doesn't push the form's own controls off-screen. */
export function AssetPickerBox({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("max-h-80 overflow-y-auto rounded-lg border border-border p-2", className)}>
      {children}
    </div>
  );
}

export interface AssetPickerRowProps {
  asset: AssetResponse;
  selected?: boolean;
  disabled?: boolean;
  /** Overrides the default Add/Remove label -- Assemble's ordered buckets have
   * a third state ("Added", removable only from the chip). */
  buttonLabel?: string;
  onToggle: () => void;
}

/** One candidate row: a real inline preview (see AssetPreview's `row` variant)
 * next to its label, so an asset can be seen or heard before it's picked
 * rather than judged by an often-truncated prompt string. */
export function AssetPickerRow({
  asset,
  selected = false,
  disabled = false,
  buttonLabel,
  onToggle,
}: AssetPickerRowProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-md p-1.5",
        selected ? "bg-success/15" : "hover:bg-accent",
      )}
    >
      <AssetPreview asset={asset} variant="row" />
      <span className="min-w-0 flex-1 truncate text-sm">{assetPickerLabel(asset)}</span>
      <Button
        type="button"
        size="sm"
        variant={selected ? "secondary" : "outline"}
        disabled={disabled}
        onClick={onToggle}
      >
        {buttonLabel ?? (selected ? "Remove" : "Add")}
      </Button>
    </div>
  );
}

export function AssetPickerEmpty({ children }: { children: React.ReactNode }) {
  return <p className="p-2 text-sm text-muted-foreground">{children}</p>;
}

export interface SingleAssetPickerProps {
  assets: AssetResponse[] | undefined;
  isLoading?: boolean;
  selectedId: string | null;
  onChange: (assetId: string | null) => void;
  emptyText: string;
}

/** Pick exactly one asset, with the choice echoed back as a removable chip --
 * the Photo tab's library input and the Music tab's two remix inputs (song to
 * remix, reference track for style transfer) are all this same shape. */
export function SingleAssetPicker({
  assets,
  isLoading,
  selectedId,
  onChange,
  emptyText,
}: SingleAssetPickerProps) {
  const selectedAsset = assets?.find((asset) => asset.id === selectedId) ?? null;

  return (
    <div className="space-y-2">
      <AssetPickerBox>
        {isLoading && <AssetPickerEmpty>Loading…</AssetPickerEmpty>}
        {!isLoading && (!assets || assets.length === 0) && (
          <AssetPickerEmpty>{emptyText}</AssetPickerEmpty>
        )}
        {assets?.map((asset) => (
          <AssetPickerRow
            key={asset.id}
            asset={asset}
            selected={asset.id === selectedId}
            onToggle={() => onChange(asset.id === selectedId ? null : asset.id)}
          />
        ))}
      </AssetPickerBox>
      {selectedAsset && (
        <ChipRow>
          <Chip label={assetPickerLabel(selectedAsset)} onRemove={() => onChange(null)} />
        </ChipRow>
      )}
    </div>
  );
}

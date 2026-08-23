import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";

import type { AssetResponse } from "@/client";
import { GenerationService } from "@/client";
import { AssetPickerBox, AssetPickerEmpty, AssetPickerRow } from "@/components/AssetPicker";
import { Chip, ChipRow } from "@/components/Chip";
import { JobResult } from "@/components/JobResult";
import { ProjectFilterSelect } from "@/components/ProjectSelect";
import { Button } from "@/components/ui/button";
import { Hint, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useJobRunner } from "@/hooks/useJobRunner";
import { useLibraryAssets } from "@/hooks/useLibrary";
import { assetMediaKind, assetPickerLabel } from "@/lib/assets";
import { numberField } from "@/lib/form";

export const Route = createFileRoute("/ui/assemble")({
  component: AssembleTab,
});

/** Ordered buckets (clips, music) take playback order from click order, which
 * is what POST /assemble's own list-in-order contract expects. Sound effects
 * are unordered -- they mix together continuously underneath, so order carries
 * no meaning there. */
type Bucket = "clips" | "music" | "sfx";

/** An ordered bucket can legitimately hold the same asset twice (play a clip
 * again later in the reel), so a selection entry carries its own identity
 * rather than being keyed by asset id or list position -- otherwise removing
 * one occurrence is ambiguous. */
interface SelectedAsset {
  key: string;
  assetId: string;
}

function AssembleTab() {
  const runner = useJobRunner();
  const [projectFilter, setProjectFilter] = useState("");
  // No `kind` filter: POST /assemble resolves any asset id via
  // asset_library.get() regardless of kind, so a hand-uploaded reference
  // track (or source clip) is just as usable here as a generated one --
  // restricting to kind="generated" would silently hide it.
  const {
    data: assets,
    isPending,
    refetch,
  } = useLibraryAssets({
    project: projectFilter,
  });

  const [selection, setSelection] = useState<Record<Bucket, SelectedAsset[]>>({
    clips: [],
    music: [],
    sfx: [],
  });
  const [videoCrossfade, setVideoCrossfade] = useState("1.0");
  const [musicCrossfade, setMusicCrossfade] = useState("5.0");
  const [musicEdgeFade, setMusicEdgeFade] = useState("2.0");
  const [musicGap, setMusicGap] = useState("0");

  // Chips outlive the list they were picked from (changing the project filter
  // refetches a narrower set), so every asset ever seen is kept by id purely so
  // a selected chip can still render its own label.
  const seenAssets = useRef<Record<string, AssetResponse>>({});
  useEffect(() => {
    for (const asset of assets ?? []) seenAssets.current[asset.id] = asset;
  }, [assets]);

  /** Buckets every library asset (any kind) by the same extension
   * classification the previews use: video extensions are clip candidates,
   * audio extensions are music candidates unless tagged "sound-effect".
   * Anything else (a mask PNG, say) is simply not offered. */
  const buckets = useMemo(() => {
    const result: Record<Bucket, AssetResponse[]> = { clips: [], music: [], sfx: [] };
    for (const asset of assets ?? []) {
      const kind = assetMediaKind(asset);
      if (kind === "video") result.clips.push(asset);
      else if (kind === "audio") {
        result[asset.tags.includes("sound-effect") ? "sfx" : "music"].push(asset);
      }
    }
    return result;
  }, [assets]);

  const toggle = (bucket: Bucket, assetId: string, allowMultiple: boolean) => {
    setSelection((prev) => {
      const current = prev[bucket];
      const next =
        allowMultiple && current.some((entry) => entry.assetId === assetId)
          ? current.filter((entry) => entry.assetId !== assetId)
          : [...current, { key: crypto.randomUUID(), assetId }];
      return { ...prev, [bucket]: next };
    });
  };

  const remove = (bucket: Bucket, key: string) => {
    setSelection((prev) => ({
      ...prev,
      [bucket]: prev[bucket].filter((entry) => entry.key !== key),
    }));
  };

  const submit = () => {
    if (selection.clips.length === 0 || selection.music.length === 0) {
      runner.fail("Pick at least one clip and one music track.");
      return;
    }
    runner.run(async () => {
      const { data } = await GenerationService.assembleAssemblePost({
        body: {
          clip_asset_ids: selection.clips.map((entry) => entry.assetId),
          music_asset_ids: selection.music.map((entry) => entry.assetId),
          sound_effect_asset_ids: selection.sfx.map((entry) => entry.assetId),
          video_crossfade_duration: numberField(videoCrossfade, 1),
          music_crossfade_duration: numberField(musicCrossfade, 5),
          music_edge_fade_duration: numberField(musicEdgeFade, 2),
          music_gap_duration: numberField(musicGap, 0),
        },
      });
      return data.job_id;
    });
  };

  const renderPicker = (bucket: Bucket, title: string, allowMultiple: boolean) => (
    <div className="space-y-2">
      <strong className="text-sm">{title}</strong>
      <AssetPickerBox>
        {isPending && <AssetPickerEmpty>Loading…</AssetPickerEmpty>}
        {!isPending && buckets[bucket].length === 0 && (
          <AssetPickerEmpty>(none available)</AssetPickerEmpty>
        )}
        {buckets[bucket].map((asset) => {
          const selected = selection[bucket].some((entry) => entry.assetId === asset.id);
          // Ordered buckets: once added, the row locks -- clicking it again
          // couldn't say *which* occurrence to drop, so removal goes through
          // the chip's own ×. Sound effects toggle freely.
          const locked = selected && !allowMultiple;
          return (
            <AssetPickerRow
              key={asset.id}
              asset={asset}
              selected={selected}
              disabled={locked}
              buttonLabel={locked ? "Added" : selected ? "Remove" : "Add"}
              onToggle={() => toggle(bucket, asset.id, allowMultiple)}
            />
          );
        })}
      </AssetPickerBox>
      <ChipRow>
        {selection[bucket].map((entry, index) => {
          const asset = seenAssets.current[entry.assetId];
          const label = asset ? assetPickerLabel(asset) : entry.assetId.slice(0, 8);
          return (
            <Chip
              key={entry.key}
              // Ordered buckets number their chips, since that order is the
              // playback order the job will use.
              label={allowMultiple ? label : `${index + 1}. ${label}`}
              onRemove={() => remove(bucket, entry.key)}
            />
          );
        })}
      </ChipRow>
    </div>
  );

  return (
    <div className="space-y-5">
      <Hint>
        Combines library assets (generated here or uploaded yourself) into one video — click to add
        each in playback order, remove from the selected list below. Sound effects (optional) mix
        together continuously under the music, order doesn't matter for those.
      </Hint>

      <div className="flex flex-wrap items-center gap-2">
        <InlineField label="Project">
          <ProjectFilterSelect
            value={projectFilter}
            onChange={setProjectFilter}
            aria-label="Project filter"
          />
        </InlineField>
        <Hint>Narrows the pickers below to one project's assets.</Hint>
      </div>

      {renderPicker("clips", "Video clips (in order)", false)}
      {renderPicker("music", "Music tracks (in order)", false)}
      {renderPicker("sfx", "Sound effects (optional)", true)}

      <div className="flex flex-wrap gap-4">
        <InlineField label="Video crossfade (s)">
          <Input
            type="number"
            min="0"
            step="0.1"
            className="w-24"
            value={videoCrossfade}
            onChange={(event) => setVideoCrossfade(event.target.value)}
          />
        </InlineField>
        <InlineField label="Music crossfade (s)">
          <Input
            type="number"
            min="0"
            step="0.1"
            className="w-24"
            value={musicCrossfade}
            onChange={(event) => setMusicCrossfade(event.target.value)}
          />
        </InlineField>
        <InlineField label="Music edge fade (s)">
          <Input
            type="number"
            min="0"
            step="0.1"
            className="w-24"
            value={musicEdgeFade}
            onChange={(event) => setMusicEdgeFade(event.target.value)}
          />
        </InlineField>
        <InlineField label="Music gap (s)">
          <Input
            type="number"
            min="0"
            step="0.1"
            className="w-24"
            value={musicGap}
            onChange={(event) => setMusicGap(event.target.value)}
          />
        </InlineField>
      </div>
      <Hint>
        Music gap: a silent pause between songs instead of crossfading them (0 = crossfade as
        usual). Sound effects keep playing continuously through the gap — only the music pauses.
      </Hint>

      <div className="flex gap-2">
        <Button type="button" variant="outline" onClick={() => refetch()}>
          Refresh assets
        </Button>
        <Button type="button" onClick={submit} disabled={runner.isRunning}>
          {runner.isRunning ? "Assembling…" : "Assemble"}
        </Button>
      </div>

      <JobResult runner={runner} preview="video" />
    </div>
  );
}

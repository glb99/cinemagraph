import { createFileRoute } from "@tanstack/react-router";
import { type FormEvent, useMemo, useRef, useState } from "react";

import { RenderService } from "@/client";
import { SingleAssetPicker } from "@/components/AssetPicker";
import { JobResult } from "@/components/JobResult";
import { Button } from "@/components/ui/button";
import { CheckboxField, Field, Fieldset, Hint, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useCapabilities, useEffects } from "@/hooks/useCapabilities";
import { useJobRunner } from "@/hooks/useJobRunner";
import { useLibraryAssets } from "@/hooks/useLibrary";
import { assetHasExtension, PHOTO_INPUT_EXTS } from "@/lib/assets";
import { numberField, optionalNumberField } from "@/lib/form";

export const Route = createFileRoute("/ui/")({
  component: PhotoTab,
});

function PhotoTab() {
  const { data: capabilities } = useCapabilities();
  const { data: effectNames, isPending: effectsPending } = useEffects();
  const { data: assets, isPending: assetsPending } = useLibraryAssets();
  const runner = useJobRunner();

  const photoInputRef = useRef<HTMLInputElement>(null);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [libraryAssetId, setLibraryAssetId] = useState<string | null>(null);
  const [effects, setEffects] = useState<string[]>([]);
  const [maskFile, setMaskFile] = useState<File | null>(null);
  const [maskPrompt, setMaskPrompt] = useState("");
  const [unmaskedEffects, setUnmaskedEffects] = useState<string[]>([]);
  const [duration, setDuration] = useState("4");
  const [fps, setFps] = useState("30");
  const [speed, setSpeed] = useState("1.0");
  const [loopDuration, setLoopDuration] = useState("");

  const photoAssets = useMemo(
    () => assets?.filter((asset) => assetHasExtension(asset, PHOTO_INPUT_EXTS)),
    [assets],
  );

  const toggleEffect = (name: string) => {
    setEffects((prev) =>
      prev.includes(name) ? prev.filter((effect) => effect !== name) : [...prev, name],
    );
  };

  const toggleUnmasked = (name: string) => {
    setUnmaskedEffects((prev) =>
      prev.includes(name) ? prev.filter((effect) => effect !== name) : [...prev, name],
    );
  };

  // Dropping an effect (or the mask itself) shouldn't leave a stale override
  // sitting in state -- only effects both selected *and* still relevant once
  // a mask exists are ever sent.
  const hasMask = !!maskFile || !!maskPrompt.trim();
  const effectiveUnmasked = unmaskedEffects.filter((name) => effects.includes(name));

  // The photo input is either a fresh upload or a library asset -- exactly one,
  // the same "pick one or the other" shape as mask file vs. mask prompt below.
  // Choosing either side clears the other here rather than letting both sit
  // filled in and surfacing the conflict at submit time.
  const pickLibraryAsset = (assetId: string | null) => {
    setLibraryAssetId(assetId);
    if (assetId) {
      setPhotoFile(null);
      if (photoInputRef.current) photoInputRef.current.value = "";
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();

    if (effects.length === 0) {
      runner.fail("Select at least one effect.");
      return;
    }
    if (!photoFile && !libraryAssetId) {
      runner.fail("Upload a photo or pick one from the library.");
      return;
    }
    if (maskFile && maskPrompt.trim()) {
      runner.fail("Supply either a mask file or a mask prompt, not both.");
      return;
    }

    runner.run(async () => {
      const { data } = await RenderService.renderPhotoRenderPhotoPost({
        body: {
          input_file: photoFile ?? undefined,
          input_asset_id: photoFile ? undefined : libraryAssetId,
          effect: effects,
          mask: maskFile ?? undefined,
          mask_prompt: maskPrompt.trim() || undefined,
          unmasked_effects: hasMask && effectiveUnmasked.length ? effectiveUnmasked : undefined,
          duration: numberField(duration, 4),
          fps: numberField(fps, 30),
          speed: numberField(speed, 1),
          loop_duration: optionalNumberField(loopDuration),
        },
      });
      return data.job_id;
    });
  };

  return (
    <form className="space-y-4" onSubmit={submit}>
      <Field label="Photo">
        <Input
          ref={photoInputRef}
          type="file"
          accept="image/*"
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            setPhotoFile(file);
            if (file) setLibraryAssetId(null);
          }}
        />
      </Field>

      <div className="space-y-2">
        <Hint>…or pick an existing photo/image from the library instead of uploading:</Hint>
        <SingleAssetPicker
          assets={photoAssets}
          isLoading={assetsPending}
          selectedId={libraryAssetId}
          onChange={pickLibraryAsset}
          emptyText="(no images in the library yet)"
        />
      </div>

      <Fieldset legend="Effects">
        <div className="flex flex-wrap gap-x-5 gap-y-2" data-testid="effect-list">
          {effectsPending && <Hint>Loading…</Hint>}
          {effectNames?.map((name) => (
            <CheckboxField
              key={name}
              label={name}
              checked={effects.includes(name)}
              onChange={() => toggleEffect(name)}
            />
          ))}
        </div>
      </Fieldset>

      <Fieldset legend="Mask (optional — omit to animate the whole photo)" className="space-y-3">
        <Field label="Hand-painted mask file">
          <Input
            type="file"
            accept="image/png"
            onChange={(event) => setMaskFile(event.target.files?.[0] ?? null)}
          />
        </Field>
        {/* Semantic masking is only offered when the ML service reports in --
            same feature gate the tabs themselves use. */}
        {capabilities?.semantic_mask && (
          <Field label="Or describe what to animate (semantic masking)">
            <Input
              type="text"
              placeholder='e.g. "clouds", "sun", "water"'
              value={maskPrompt}
              onChange={(event) => setMaskPrompt(event.target.value)}
            />
          </Field>
        )}
        {hasMask && effects.length > 0 && (
          <div className="space-y-1.5">
            <Hint>
              Apply this mask to (uncheck an effect to let it animate over the whole photo
              regardless — e.g. keep snow falling everywhere while flicker stays confined to the
              mask):
            </Hint>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              {effects.map((name) => (
                <CheckboxField
                  key={name}
                  label={name}
                  checked={!unmaskedEffects.includes(name)}
                  onChange={() => toggleUnmasked(name)}
                />
              ))}
            </div>
          </div>
        )}
      </Fieldset>

      <Fieldset legend="Timing" className="space-y-3">
        <div className="flex flex-wrap gap-4">
          <InlineField label="Duration (s)">
            <Input
              type="number"
              min="0.5"
              step="0.5"
              className="w-24"
              value={duration}
              onChange={(event) => setDuration(event.target.value)}
            />
          </InlineField>
          <InlineField label="FPS">
            <Input
              type="number"
              min="1"
              className="w-24"
              value={fps}
              onChange={(event) => setFps(event.target.value)}
            />
          </InlineField>
          <InlineField label="Speed">
            <Input
              type="number"
              min="0.1"
              step="0.1"
              className="w-24"
              value={speed}
              onChange={(event) => setSpeed(event.target.value)}
            />
          </InlineField>
          <InlineField label="Loop duration (s)">
            <Input
              type="number"
              min="1"
              step="1"
              placeholder="e.g. 3600"
              className="w-28"
              value={loopDuration}
              onChange={(event) => setLoopDuration(event.target.value)}
            />
          </InlineField>
        </div>
        <Hint>
          Stretches the output to this length by repeating the duration-long loop, instead of
          rendering unique frames the whole way (e.g. an hour-long ambient loop).
        </Hint>
      </Fieldset>

      <Button type="submit" disabled={runner.isRunning}>
        {runner.isRunning ? "Rendering…" : "Render"}
      </Button>

      <JobResult runner={runner} preview="video" />
    </form>
  );
}

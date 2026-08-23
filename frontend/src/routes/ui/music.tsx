import { createFileRoute } from "@tanstack/react-router";
import { type FormEvent, useMemo, useState } from "react";

import { GenerationService } from "@/client";
import { SingleAssetPicker } from "@/components/AssetPicker";
import { ConfigHint } from "@/components/ConfigHint";
import { EnginePicker } from "@/components/EnginePicker";
import { JobResult } from "@/components/JobResult";
import { RepaintWaveform } from "@/components/RepaintWaveform";
import { Button } from "@/components/ui/button";
import { CheckboxField, Field, Hint, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useJobRunner } from "@/hooks/useJobRunner";
import { useLibraryAssets } from "@/hooks/useLibrary";
import { assetHasExtension, REMIX_AUDIO_EXTS } from "@/lib/assets";
import { engineState, MUSIC_ENGINES } from "@/lib/engines";
import { numberField } from "@/lib/form";

export const Route = createFileRoute("/ui/music")({
  component: MusicTab,
});

const TASK_TYPES = [
  { value: "text2music", label: "Text to music" },
  { value: "cover", label: "Cover (remix an existing song)" },
  { value: "repaint", label: "Repaint (regenerate a section)" },
];

function MusicTab() {
  const { data: capabilities } = useCapabilities();
  const { data: assets, isPending: assetsPending } = useLibraryAssets();
  const runner = useJobRunner();

  const [prompt, setPrompt] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [duration, setDuration] = useState("30");
  const [thinking, setThinking] = useState(true);
  const [instrumental, setInstrumental] = useState(false);
  const [taskType, setTaskType] = useState("text2music");
  const [sourceAssetId, setSourceAssetId] = useState<string | null>(null);
  const [referenceAssetId, setReferenceAssetId] = useState<string | null>(null);
  const [coverStrength, setCoverStrength] = useState("1.0");
  const [repaintStart, setRepaintStart] = useState("0");
  const [repaintEnd, setRepaintEnd] = useState("-1");
  const [model, setModel] = useState("");

  const audioAssets = useMemo(
    () => assets?.filter((asset) => assetHasExtension(asset, REMIX_AUDIO_EXTS)),
    [assets],
  );

  // A remix is in play when the task type says so, *or* whenever a reference
  // track is picked -- style transfer is independent of task type (see
  // /generate/music's own docstring). Either way, `remixUnsupported` below
  // takes any adapter whose remix() does nothing off the table, so it can't
  // be selected here; the route rejects it server-side too.
  const isRemix = taskType !== "text2music" || !!referenceAssetId;
  const registered = capabilities?.music_generation_models ?? [];
  // Which engines can remix is the backend's answer, not a fact repeated in
  // lib/engines.ts: `music_remix_models` is already the subset whose remix()
  // actually does something (Lyria3Adapter's raises NotImplementedError), so
  // registering a third adapter needs no frontend change. Only meaningful for
  // an engine that's registered at all -- an unregistered one is "off", and
  // "set this up" is the more useful thing to say about it than "can't cover".
  const remixModels = capabilities?.music_remix_models ?? [];
  const remixUnsupported = (engine: (typeof MUSIC_ENGINES)[number]) =>
    isRemix && registered.includes(engine.id) && !remixModels.includes(engine.id)
      ? `Can't ${taskType === "repaint" ? "repaint" : "cover"} — it only writes new tracks`
      : null;
  // Two engines, checked on every render -- not worth memoizing.
  const readyEngine = MUSIC_ENGINES.find(
    (engine) =>
      engineState(engine, capabilities, registered) === "ready" && !remixUnsupported(engine),
  );
  const selectedModel = MUSIC_ENGINES.some(
    (engine) =>
      engine.id === model &&
      engineState(engine, capabilities, registered) === "ready" &&
      !remixUnsupported(engine),
  )
    ? model
    : (readyEngine?.id ?? "");

  const submit = (event: FormEvent) => {
    event.preventDefault();

    if (!prompt.trim()) {
      runner.fail("Prompt is required.");
      return;
    }
    if (taskType !== "text2music" && !sourceAssetId) {
      runner.fail(`task_type="${taskType}" needs a song picked to remix.`);
      return;
    }
    if (!selectedModel) {
      runner.fail("No engine can do this yet -- set one up first.");
      return;
    }

    runner.run(async () => {
      const { data } = await GenerationService.generateMusicGenerateMusicPost({
        body: {
          prompt: prompt.trim(),
          lyrics,
          duration: numberField(duration, 30),
          thinking,
          instrumental,
          task_type: taskType,
          src_audio_asset_id: sourceAssetId ?? undefined,
          reference_audio_asset_id: referenceAssetId ?? undefined,
          cover_strength: taskType === "cover" ? numberField(coverStrength, 1) : undefined,
          repainting_start: taskType === "repaint" ? numberField(repaintStart, 0) : undefined,
          repainting_end: taskType === "repaint" ? numberField(repaintEnd, -1) : undefined,
          model: selectedModel,
        },
      });
      return data.job_id;
    });
  };

  return (
    <form className="space-y-4" onSubmit={submit}>
      <ConfigHint tab="/ui/music" />

      <Field label="Prompt">
        <Input
          type="text"
          placeholder='e.g. "ambient synth pad, slow, sci-fi"'
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </Field>

      <Field label="Lyrics (optional)">
        <Textarea value={lyrics} onChange={(event) => setLyrics(event.target.value)} />
      </Field>

      <div className="flex flex-wrap items-center gap-4">
        <InlineField label="Duration (s)">
          <Input
            type="number"
            min="5"
            className="w-24"
            value={duration}
            onChange={(event) => setDuration(event.target.value)}
          />
        </InlineField>
        <CheckboxField
          label="Thinking mode"
          checked={thinking}
          onChange={(event) => setThinking(event.target.checked)}
        />
        <CheckboxField
          label="Instrumental (no vocals)"
          checked={instrumental}
          onChange={(event) => setInstrumental(event.target.checked)}
        />
      </div>

      <InlineField label="Task type">
        <Select value={taskType} onChange={(event) => setTaskType(event.target.value)}>
          {TASK_TYPES.map((task) => (
            <option key={task.value} value={task.value}>
              {task.label}
            </option>
          ))}
        </Select>
      </InlineField>

      <Field label="Engine" hint={isRemix ? "Only one can do covers and repaints." : undefined}>
        <EnginePicker
          engines={MUSIC_ENGINES}
          registered={registered}
          capabilities={capabilities}
          selected={selectedModel}
          onSelect={setModel}
          unsupportedReason={remixUnsupported}
        />
      </Field>

      {taskType !== "text2music" && (
        <div className="space-y-2">
          <Hint>Song to remix, from the library:</Hint>
          <SingleAssetPicker
            assets={audioAssets}
            isLoading={assetsPending}
            selectedId={sourceAssetId}
            onChange={setSourceAssetId}
            emptyText="(no songs in the library yet)"
          />
        </div>
      )}

      {taskType === "cover" && (
        <InlineField label="Cover strength">
          <Input
            type="number"
            min="0"
            max="1"
            step="0.05"
            className="w-24"
            value={coverStrength}
            onChange={(event) => setCoverStrength(event.target.value)}
          />
        </InlineField>
      )}

      {taskType === "repaint" && (
        <div className="space-y-2">
          <Hint>
            Drag on the waveform to select the section to repaint (or type the times below):
          </Hint>
          <RepaintWaveform
            assetId={sourceAssetId}
            start={repaintStart}
            end={repaintEnd}
            onChange={(start, end) => {
              setRepaintStart(start);
              setRepaintEnd(end);
            }}
          />
          <div className="flex flex-wrap items-center gap-4">
            <InlineField label="Start (s)">
              <Input
                type="number"
                min="0"
                step="0.1"
                className="w-24"
                value={repaintStart}
                onChange={(event) => setRepaintStart(event.target.value)}
              />
            </InlineField>
            <InlineField label="End (s, -1 = to end)">
              <Input
                type="number"
                step="0.1"
                className="w-24"
                value={repaintEnd}
                onChange={(event) => setRepaintEnd(event.target.value)}
              />
            </InlineField>
          </div>
        </div>
      )}

      <div className="space-y-2">
        <Hint>Optional: reference audio for style transfer (independent of task type):</Hint>
        <SingleAssetPicker
          assets={audioAssets}
          isLoading={assetsPending}
          selectedId={referenceAssetId}
          onChange={setReferenceAssetId}
          emptyText="(no songs in the library yet)"
        />
      </div>

      <Button type="submit" disabled={runner.isRunning}>
        {runner.isRunning ? "Generating…" : "Generate"}
      </Button>

      <JobResult runner={runner} preview="audio" />
    </form>
  );
}

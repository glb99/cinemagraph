import { createFileRoute } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { DefaultService } from "@/client";
import { JobResult } from "@/components/JobResult";
import { Button } from "@/components/ui/button";
import { CheckboxField, Field, Fieldset, Hint, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useJobRunner } from "@/hooks/useJobRunner";
import { numberField, optionalNumberField } from "@/lib/form";

export const Route = createFileRoute("/ui/video")({
  component: VideoTab,
});

function VideoTab() {
  const runner = useJobRunner();

  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [maskFile, setMaskFile] = useState<File | null>(null);
  const [maskThreshold, setMaskThreshold] = useState("25");
  const [stillFrameIndex, setStillFrameIndex] = useState("0");
  const [blendFrames, setBlendFrames] = useState("10");
  const [autoTrim, setAutoTrim] = useState(true);
  const [alsoGif, setAlsoGif] = useState(false);
  const [loopDuration, setLoopDuration] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();

    if (!videoFile) {
      runner.fail("Choose a video to render.");
      return;
    }
    // Also enforced server-side (and on the CLI) -- repeating the loop can't
    // produce a GIF, so catching it here saves a round trip for a known 422.
    if (loopDuration.trim() && alsoGif) {
      runner.fail("Loop duration isn't compatible with .gif export.");
      return;
    }

    runner.run(async () => {
      const { data } = await DefaultService.renderVideoRenderVideoPost({
        body: {
          input_file: videoFile,
          mask: maskFile ?? undefined,
          mask_threshold: numberField(maskThreshold, 25),
          still_frame_index: numberField(stillFrameIndex, 0),
          blend_frames: numberField(blendFrames, 10),
          auto_trim: autoTrim,
          also_gif: alsoGif,
          loop_duration: optionalNumberField(loopDuration),
        },
      });
      return data.job_id;
    });
  };

  return (
    <form className="space-y-4" onSubmit={submit}>
      <Field label="Video">
        <Input
          type="file"
          accept="video/*"
          required
          onChange={(event) => setVideoFile(event.target.files?.[0] ?? null)}
        />
      </Field>

      <Fieldset legend="Mask (optional — omit to auto-detect motion)" className="space-y-3">
        <Field label="Hand-painted mask file">
          <Input
            type="file"
            accept="image/png"
            onChange={(event) => setMaskFile(event.target.files?.[0] ?? null)}
          />
        </Field>
        <InlineField label="Mask threshold">
          <Input
            type="number"
            min="1"
            className="w-24"
            value={maskThreshold}
            onChange={(event) => setMaskThreshold(event.target.value)}
          />
        </InlineField>
      </Fieldset>

      <Fieldset legend="Loop">
        <div className="flex flex-wrap items-center gap-4">
          <InlineField label="Still frame index">
            <Input
              type="number"
              min="0"
              className="w-24"
              value={stillFrameIndex}
              onChange={(event) => setStillFrameIndex(event.target.value)}
            />
          </InlineField>
          <InlineField label="Blend frames">
            <Input
              type="number"
              min="0"
              className="w-24"
              value={blendFrames}
              onChange={(event) => setBlendFrames(event.target.value)}
            />
          </InlineField>
          <CheckboxField
            label="Auto-trim to best loop point"
            checked={autoTrim}
            onChange={(event) => setAutoTrim(event.target.checked)}
          />
        </div>
      </Fieldset>

      <Fieldset legend="Output" className="space-y-3">
        <div className="flex flex-wrap items-center gap-4">
          <CheckboxField
            label="Also export .gif"
            checked={alsoGif}
            onChange={(event) => setAlsoGif(event.target.checked)}
          />
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
          Stretches the output to this length by repeating the detected loop, instead of rendering
          unique frames the whole way (e.g. an hour-long ambient loop from a few seconds of source).
          Not compatible with .gif export.
        </Hint>
      </Fieldset>

      <Button type="submit" disabled={runner.isRunning}>
        {runner.isRunning ? "Rendering…" : "Render"}
      </Button>

      <JobResult runner={runner} preview="video" />
    </form>
  );
}

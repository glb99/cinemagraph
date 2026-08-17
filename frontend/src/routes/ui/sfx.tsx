import { createFileRoute } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { DefaultService } from "@/client";
import { ConfigHint } from "@/components/ConfigHint";
import { JobResult } from "@/components/JobResult";
import { Button } from "@/components/ui/button";
import { Field, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useJobRunner } from "@/hooks/useJobRunner";
import { numberField } from "@/lib/form";

export const Route = createFileRoute("/ui/sfx")({
  component: SoundEffectTab,
});

function SoundEffectTab() {
  const runner = useJobRunner();
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState("10");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) {
      runner.fail("Prompt is required.");
      return;
    }
    runner.run(async () => {
      const { data } = await DefaultService.generateSoundEffectGenerateSoundEffectPost({
        body: { prompt: prompt.trim(), duration: numberField(duration, 10) },
      });
      return data.job_id;
    });
  };

  return (
    <form className="space-y-4" onSubmit={submit}>
      <ConfigHint tab="/ui/sfx" />

      <Field label="Prompt">
        <Input
          type="text"
          placeholder='e.g. "gentle wind chimes in a light breeze"'
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </Field>

      {/* Stable Audio Open's own ceiling, not an arbitrary cap here. */}
      <InlineField label="Duration (s)">
        <Input
          type="number"
          min="1"
          max="47"
          className="w-24"
          value={duration}
          onChange={(event) => setDuration(event.target.value)}
        />
      </InlineField>

      <Button type="submit" disabled={runner.isRunning}>
        {runner.isRunning ? "Generating…" : "Generate"}
      </Button>

      <JobResult runner={runner} preview="audio" />
    </form>
  );
}

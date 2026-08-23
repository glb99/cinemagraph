import { createFileRoute } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { DefaultService } from "@/client";
import { ConfigHint } from "@/components/ConfigHint";
import { EnginePicker } from "@/components/EnginePicker";
import { JobResult } from "@/components/JobResult";
import { Button } from "@/components/ui/button";
import { Field, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useJobRunner } from "@/hooks/useJobRunner";
import { IMAGE_ENGINES, engineState } from "@/lib/engines";
import { numberField } from "@/lib/form";

export const Route = createFileRoute("/ui/image")({
  component: ImageTab,
});

function ImageTab() {
  const { data: capabilities } = useCapabilities();
  const runner = useJobRunner();

  const [prompt, setPrompt] = useState("");
  const [referenceImage, setReferenceImage] = useState<File | null>(null);
  const [strength, setStrength] = useState("0.6");
  const [model, setModel] = useState("");

  const registered = capabilities?.image_generation_models ?? [];
  const readyEngine = IMAGE_ENGINES.find(
    (engine) => engineState(engine, capabilities, registered) === "ready",
  );
  const selectedModel = IMAGE_ENGINES.some(
    (engine) => engine.id === model && engineState(engine, capabilities, registered) === "ready",
  )
    ? model
    : (readyEngine?.id ?? "");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) {
      runner.fail("Prompt is required.");
      return;
    }
    if (!selectedModel) {
      runner.fail("No image engine is ready -- set one up first.");
      return;
    }
    runner.run(async () => {
      const { data } = await DefaultService.generateImageGenerateImagePost({
        body: {
          prompt: prompt.trim(),
          // strength only means anything alongside a reference image (img2img);
          // sent together or not at all, matching the route's own contract.
          reference_image: referenceImage ?? undefined,
          strength: referenceImage ? numberField(strength, 0.6) : undefined,
          model: selectedModel,
        },
      });
      return data.job_id;
    });
  };

  return (
    <form className="space-y-4" onSubmit={submit}>
      <ConfigHint tab="/ui/image" />

      <Field label="Prompt">
        <Input
          type="text"
          placeholder='e.g. "a lofi bedroom at sunset, warm light"'
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </Field>

      <Field label="Reference image (optional — guides the result instead of starting from noise)">
        <Input
          type="file"
          accept="image/*"
          onChange={(event) => setReferenceImage(event.target.files?.[0] ?? null)}
        />
      </Field>

      <InlineField label="Strength">
        <Input
          type="number"
          min="0"
          max="1"
          step="0.05"
          className="w-24"
          value={strength}
          onChange={(event) => setStrength(event.target.value)}
        />
      </InlineField>

      <Field label="Engine">
        <EnginePicker
          engines={IMAGE_ENGINES}
          registered={registered}
          capabilities={capabilities}
          selected={selectedModel}
          onSelect={setModel}
        />
      </Field>

      <Button type="submit" disabled={runner.isRunning}>
        {runner.isRunning ? "Generating…" : "Generate"}
      </Button>

      <JobResult runner={runner} preview="image" />
    </form>
  );
}

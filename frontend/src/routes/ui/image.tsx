import { createFileRoute } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { DefaultService } from "@/client";
import { ConfigHint } from "@/components/ConfigHint";
import { JobResult } from "@/components/JobResult";
import { Button } from "@/components/ui/button";
import { Field, InlineField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useJobRunner } from "@/hooks/useJobRunner";
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

  const modelOptions = capabilities?.image_generation_models ?? [];
  // Same rule as the music tab: the picker only appears once a second adapter
  // is registered, since a choice of one is no choice.
  const showModelPicker = modelOptions.length > 1;
  const selectedModel = modelOptions.includes(model) ? model : (modelOptions[0] ?? "");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) {
      runner.fail("Prompt is required.");
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
          model: showModelPicker ? selectedModel : undefined,
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

      {showModelPicker && (
        <InlineField label="Model">
          <Select value={selectedModel} onChange={(event) => setModel(event.target.value)}>
            {modelOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </InlineField>
      )}

      <Button type="submit" disabled={runner.isRunning}>
        {runner.isRunning ? "Generating…" : "Generate"}
      </Button>

      <JobResult runner={runner} preview="image" />
    </form>
  );
}

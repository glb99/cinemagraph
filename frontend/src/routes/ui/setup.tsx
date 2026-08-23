import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { type ServiceStatus, SetupService } from "@/client";
import { Button } from "@/components/ui/button";
import { ErrorText } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useCapabilities } from "@/hooks/useCapabilities";
import { errorMessage } from "@/lib/api";
import { SERVICE_START_COMMANDS, TABS, tabAvailability } from "@/lib/tabs";

export const Route = createFileRoute("/ui/setup")({
  component: SetupTab,
});

type State = "on" | "down" | "stuck" | "off";

const STATE_STYLES: Record<State, string> = {
  on: "border-success/40 bg-success/10 text-success-foreground",
  down: "border-warning/40 bg-warning-surface text-warning-foreground",
  stuck: "border-warning/40 bg-warning-surface text-warning-foreground",
  off: "border-border bg-muted text-muted-foreground",
};

const STATE_LABELS: Record<State, string> = {
  on: "Running",
  down: "Not running",
  stuck: "Stuck",
  off: "Not configured",
};

/** What each local satellite is for, alongside its live status -- the thing
 * a bare env-var name and a dot can't say on their own. Sizes/GPU needs
 * aren't in the API response (they're deployment facts, not runtime state),
 * so this stays a small frontend-side table next to SERVICE_START_COMMANDS
 * in lib/tabs.ts rather than a new field on ServiceStatus. */
const SERVICE_BLURB: Record<string, string> = {
  IMAGE_GENERATION_URL: "SDXL — image generation, needs a GPU",
  ACESTEP_URL: "ACE-Step — music generation, needs a GPU",
  SOUND_EFFECTS_URL: "Stable Audio Open — sound effects, needs a GPU and its licence accepted",
  ML_SERVICE_URL: "CLIPSeg — mask by describing what to animate, runs on CPU",
};

function Badge({ state }: { state: State }) {
  return (
    <span
      className={`shrink-0 rounded-full border px-2 py-0.5 font-medium text-xs ${STATE_STYLES[state]}`}
    >
      {STATE_LABELS[state]}
    </span>
  );
}

function serviceState(service: ServiceStatus): State {
  if (service.reachable) return "on";
  if (!service.configured) return "off";
  // Configured, not reachable, but it *answered* -- that's the busy watchdog's
  // 503, not a missing container. Different problem, different advice.
  return service.health?.status === "stuck" ? "stuck" : "down";
}

/** What the satellite's own /health said, in words rather than raw JSON. */
function healthSummary(service: ServiceStatus): string | null {
  const health = service.health;
  if (!health) return null;
  const parts: string[] = [];
  if (typeof health.device === "string") parts.push(health.device);
  if (typeof health.model_loaded === "boolean") {
    parts.push(health.model_loaded ? "model loaded" : "model unloaded (idle)");
  }
  if (typeof health.inflight_seconds === "number") {
    parts.push(`busy for ${Math.round(health.inflight_seconds)}s`);
  }
  return parts.length ? parts.join(" · ") : null;
}

function ServiceRow({ service }: { service: ServiceStatus }) {
  const state = serviceState(service);
  const summary = healthSummary(service);
  const startCommand = SERVICE_START_COMMANDS[service.env_var];

  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-medium text-sm">{service.name}</span>
        <Badge state={state} />
      </div>
      {SERVICE_BLURB[service.env_var] ? (
        <p className="text-xs">{SERVICE_BLURB[service.env_var]}</p>
      ) : null}
      <div className="text-muted-foreground text-xs">
        <code className="font-mono">{service.env_var}</code>
        {summary ? <> — {summary}</> : null}
      </div>
      {state === "off" && startCommand ? (
        <p className="text-muted-foreground text-xs">
          Not enabled. Start it with <code className="font-mono">{startCommand}</code>, then set{" "}
          <code className="font-mono">{service.env_var}</code>.
        </p>
      ) : null}
      {state === "down" && startCommand ? (
        <p className="text-warning-foreground text-xs">
          Configured, but nothing is answering — the container probably isn't running. Start it with{" "}
          <code className="font-mono">{startCommand}</code>.
        </p>
      ) : null}
      {state === "stuck" ? (
        <p className="text-warning-foreground text-xs">
          A request has been running long enough that the service considers itself wedged. It kills
          and restarts itself once <code className="font-mono">BUSY_TIMEOUT</code> elapses — no
          action needed unless this keeps happening.
        </p>
      ) : null}
    </li>
  );
}

/** Stores GEMINI_API_KEY in the API's dotenv file.
 *
 * Write-only by design: there's no route that reads a key back, so this can
 * report "set" but never show what was set, and it starts empty every time.
 * The API is also explicit that a stored key applies at its *next* start --
 * settings are read once per process (see server/config.py) -- so this says
 * "restart" rather than pretending the change took effect. */
function GeminiKeyForm({ isSet }: { isSet: boolean }) {
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSaved(null);
    setSaving(true);
    try {
      const { data } = await SetupService.storeGeminiKeySetupGeminiKeyPost({
        body: { api_key: key.trim() },
      });
      // Never keep the value around once it's been handed over.
      setKey("");
      setSaved(
        data.is_set
          ? `Saved to ${data.env_file}. Restart the API to start using it.`
          : `Removed from ${data.env_file}. Restart the API to apply.`,
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="password"
          className="min-w-56 flex-1"
          // Browsers offer to remember anything they read as a credential; this
          // one already lives in a file on the same machine.
          autoComplete="off"
          placeholder={isSet ? "Replace the stored key" : "Paste your Google AI key"}
          aria-label="Google AI key"
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
        <Button type="submit" disabled={saving || (!key.trim() && !isSet)}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        Stored in the API's <code className="font-mono">.env</code>, which is gitignored. Leave the
        box empty and press Save to remove it.
      </p>
      <ErrorText>{error}</ErrorText>
      {saved ? <p className="text-success text-xs">{saved}</p> : null}
    </form>
  );
}

function SetupTab() {
  const { data: capabilities, isPending, isFetching } = useCapabilities();
  const queryClient = useQueryClient();

  // The capabilities query is cached for the session (satellites don't usually
  // come and go mid-visit), which is exactly wrong for this tab: you come here
  // *because* you just started a container. Invalidating the shared key also
  // updates the nav and every other tab, which is the behaviour you want.
  const recheck = () => queryClient.invalidateQueries({ queryKey: ["capabilities"] });

  if (isPending) return <p className="text-muted-foreground text-sm">Checking…</p>;
  if (!capabilities) {
    return (
      <p className="text-sm text-warning-foreground">
        Couldn't reach the API to check what's available.
      </p>
    );
  }

  const features = TABS.filter((tab) => tab.capability);
  const services = capabilities.services ?? [];
  // Neither adapter has its own health check (it's a single hosted API call,
  // not a satellite) -- registration is the only signal, and both register
  // together off the one GEMINI_API_KEY (see app.py's registration block).
  const geminiConfigured = (capabilities.image_generation_models ?? []).includes("gemini");

  return (
    <div className="space-y-8">
      <section className="space-y-2">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-semibold text-base">Setup</h2>
          <Button type="button" onClick={recheck} disabled={isFetching}>
            {isFetching ? "Checking…" : "Recheck"}
          </Button>
        </div>
        <p className="text-muted-foreground text-sm">
          Everything optional is off until you enable it, and nothing here is required — photo and
          video rendering, the library, and assembly all work with every row below switched off.
        </p>
        {capabilities.version ? (
          <p className="text-muted-foreground text-xs">
            Version <code className="font-mono">{capabilities.version}</code>
          </p>
        ) : null}
      </section>

      <section className="space-y-2">
        <h3 className="font-medium text-sm">Hosted</h3>
        <p className="text-muted-foreground text-xs">
          Runs on Google's hardware — no GPU needed, billed to your key.
        </p>
        <div className="rounded-lg border border-border p-4">
          <div className="flex items-baseline justify-between gap-3">
            <div>
              <p className="font-medium text-sm">Google AI key</p>
              <p className="text-xs">Enables Gemini for images and Lyria 3 for music.</p>
              <p className="mt-1 text-muted-foreground text-xs">
                <code className="font-mono">GEMINI_API_KEY</code>
              </p>
            </div>
            <Badge state={geminiConfigured ? "on" : "off"} />
          </div>
          <GeminiKeyForm isSet={geminiConfigured} />
        </div>
      </section>

      <section className="space-y-2">
        <h3 className="font-medium text-sm">On this machine</h3>
        <p className="text-muted-foreground text-xs">
          Free and private, but each one downloads a model and wants a GPU — an 8&nbsp;GB card means
          image generation and the audio models take turns rather than running together (see the
          justfile's <code className="font-mono">gpu-image</code>/
          <code className="font-mono">gpu-audio</code> recipes).
        </p>
        <ul className="divide-y divide-border rounded-lg border border-border">
          {services.map((service) => (
            <ServiceRow key={service.env_var} service={service} />
          ))}
        </ul>
      </section>

      <section className="space-y-2">
        <h3 className="font-medium text-sm">Features</h3>
        <ul>
          {features.map((tab) => {
            const { available, configured } = tabAvailability(tab, capabilities);
            const state: State = available ? "on" : configured ? "down" : "off";
            return (
              <li
                key={tab.to}
                className="flex items-baseline justify-between gap-3 border-border border-b py-2 last:border-b-0"
              >
                <span className="text-sm">{tab.label}</span>
                <Badge state={state} />
              </li>
            );
          })}
        </ul>
        <p className="text-muted-foreground text-xs">
          A feature can be on through more than one backend — image generation and music also work
          with a <code className="font-mono">GEMINI_API_KEY</code> and no container at all.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="font-medium text-sm">From a terminal</h3>
        <p className="text-muted-foreground text-sm">
          <code className="font-mono">cinemagraph doctor</code> reports the same thing, plus local
          checks this page can't see — ffmpeg, writable directories, and whether a GPU is visible.
        </p>
      </section>
    </div>
  );
}

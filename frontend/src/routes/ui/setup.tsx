import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import type { ServiceStatus } from "@/client";
import { Button } from "@/components/ui/button";
import { useCapabilities } from "@/hooks/useCapabilities";
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
    <li className="flex flex-col gap-2 border-border border-b py-3 last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-medium text-sm">{service.name}</span>
        <Badge state={state} />
      </div>
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
        <h3 className="font-medium text-sm">Services</h3>
        <ul>
          {capabilities.services?.map((service) => (
            <ServiceRow key={service.env_var} service={service} />
          ))}
        </ul>
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

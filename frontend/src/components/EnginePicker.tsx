import { Link } from "@tanstack/react-router";

import type { CapabilitiesResponse } from "@/client";
import { NavIcon } from "@/components/NavIcon";
import { type EngineDef, type EngineState, engineState } from "@/lib/engines";
import { cn } from "@/lib/utils";

const DOT: Record<EngineState, string> = {
  ready: "bg-success",
  down: "bg-warning",
  off: "bg-muted-foreground/40",
};

function stateTag(engine: EngineDef, state: EngineState): string {
  if (state === "ready") return "Running";
  if (state === "down") return "Not running";
  return engine.where === "hosted" ? "Key needed" : "Not set up";
}

export interface EnginePickerProps {
  engines: EngineDef[];
  /** The live `*_generation_models` list -- see lib/engines.ts's `engineState`. */
  registered: string[];
  capabilities: CapabilitiesResponse | undefined;
  selected: string;
  onSelect: (id: string) => void;
  /** Per-engine reason it can't handle whatever's currently being asked of it
   * (e.g. Music's cover/repaint tasks on an engine whose adapter never
   * implements remix()) -- independent of whether the engine itself is up,
   * so it overrides an otherwise-ready engine's normal cost line. */
  unsupportedReason?: (engine: EngineDef) => string | null;
}

/** Local-vs-hosted engine choice: names where each option runs, what it costs,
 * and -- for whichever one can't be used right now -- why not, instead of a
 * bare dropdown of adapter ids. Every known engine is always listed, whether
 * or not it's currently usable; see lib/engines.ts's module docstring. */
export function EnginePicker({
  engines,
  registered,
  capabilities,
  selected,
  onSelect,
  unsupportedReason,
}: EnginePickerProps) {
  const groupName = `engine-${engines.map((engine) => engine.id).join("-")}`;

  return (
    <div className="space-y-1.5" role="radiogroup">
      {engines.map((engine) => {
        const state = engineState(engine, capabilities, registered);
        const reason = unsupportedReason?.(engine) ?? null;
        const usable = state === "ready" && !reason;
        const isSelected = usable && engine.id === selected;
        const subtitle = reason ?? engine.cost;

        const body = (
          <>
            <NavIcon name={engine.where === "local" ? "server" : "cloud"} size={17} />
            <div className="min-w-0 flex-1 space-y-0.5 text-left">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{engine.label}</span>
                <span className="rounded border border-border px-1 py-0.5 font-mono text-[10px] text-muted-foreground uppercase tracking-wide">
                  {stateTag(engine, state)}
                </span>
              </div>
              <p
                className={cn(
                  "text-xs",
                  reason ? "text-warning-foreground" : "text-muted-foreground",
                )}
              >
                {subtitle}
              </p>
            </div>
          </>
        );

        if (!usable) {
          return (
            <Link
              key={engine.id}
              to="/ui/setup"
              className="flex items-center gap-3 rounded-lg border border-border p-3 opacity-70 transition-opacity hover:opacity-100"
            >
              {body}
              {!reason && (
                <span className="shrink-0 text-xs font-medium text-primary">
                  {state === "off" && engine.where === "hosted" ? "Add key" : "Set up"} →
                </span>
              )}
            </Link>
          );
        }

        return (
          <label
            key={engine.id}
            className={cn(
              "flex w-full cursor-pointer items-center gap-3 rounded-lg border p-3",
              isSelected ? "border-primary bg-primary/10" : "border-border hover:bg-accent",
            )}
          >
            <input
              type="radio"
              name={groupName}
              className="sr-only"
              checked={isSelected}
              onChange={() => onSelect(engine.id)}
            />
            {body}
            <span className={cn("block size-2 shrink-0 rounded-full", DOT[state])} />
          </label>
        );
      })}
    </div>
  );
}

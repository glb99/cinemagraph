import { useCapabilities } from "@/hooks/useCapabilities";
import { TABS, tabAvailability } from "@/lib/tabs";
import type { TabDef } from "@/lib/tabs";

/** "Configured but not reachable right now" -- typically the satellite's
 * container just isn't running. Distinct from "not configured at all", which
 * hides the tab entirely: there's nothing actionable to hint about in that case
 * without redeploying. See app.py's /capabilities docstring. */
export function ConfigHint({ tab }: { tab: TabDef["to"] }) {
  const { data: capabilities } = useCapabilities();
  const def = TABS.find((candidate) => candidate.to === tab);
  if (!def) return null;

  const { available, configured } = tabAvailability(def, capabilities);
  if (available || !configured || !def.startCommand) return null;

  return (
    <div className="rounded-lg border border-warning/40 bg-warning-surface px-3 py-2 text-sm text-warning-foreground">
      {def.label} is configured but not reachable right now — its container probably isn't running.
      Start it with: <code className="font-mono">{def.startCommand}</code>
    </div>
  );
}

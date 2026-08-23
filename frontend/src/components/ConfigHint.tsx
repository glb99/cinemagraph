import { useCapabilities } from "@/hooks/useCapabilities";
import type { TabDef } from "@/lib/tabs";
import { TABS, tabAvailability } from "@/lib/tabs";

/** Explains, in place, why a feature can't run yet.
 *
 * Two cases now, not one. Tabs used to disappear when their satellite wasn't
 * configured, so the only case worth a hint was "configured but not reachable"
 * -- anything else had no tab to put a hint on. Now every feature is always
 * listed, which means an unconfigured one is reachable and would otherwise
 * render a working-looking form that quietly can't submit. That's a worse
 * failure than the vanishing tab it replaced, so it gets the louder message:
 *
 *   - configured, not reachable -> the container probably isn't running
 *   - not configured at all     -> this needs setting up first, here's how
 *
 * See app.py's /capabilities docstring for the configured/available split. */
export function ConfigHint({ tab }: { tab: TabDef["to"] }) {
  const { data: capabilities } = useCapabilities();
  const def = TABS.find((candidate) => candidate.to === tab);
  if (!def) return null;

  const { available, configured } = tabAvailability(def, capabilities);
  // Still loading, or genuinely fine: say nothing.
  if (available || !capabilities) return null;

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-warning/40 bg-warning-surface px-3 py-2.5 text-sm text-warning-foreground">
      {configured ? (
        <span>
          {def.label} is configured but not reachable right now — its container probably isn't
          running.
        </span>
      ) : (
        <span>
          {def.label} isn't set up yet, so this form can't run. Everything else in the app keeps
          working.
        </span>
      )}
      {def.startCommand ? (
        <span className="text-xs">
          Start it with <code className="font-mono">{def.startCommand}</code>
        </span>
      ) : null}
    </div>
  );
}

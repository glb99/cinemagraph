import { Link, Outlet, createRootRoute } from "@tanstack/react-router";

import { NavIcon } from "@/components/NavIcon";
import { useCapabilities } from "@/hooks/useCapabilities";
import { TABS, type TabDef, type TabStatus, tabStatus } from "@/lib/tabs";

export const Route = createRootRoute({
  component: RootLayout,
});

const DOT: Record<Exclude<TabStatus, "none">, { className: string; title: string }> = {
  ready: { className: "bg-success", title: "Ready" },
  down: { className: "bg-warning", title: "Configured, but not running" },
  off: { className: "bg-muted-foreground/40", title: "Not set up yet" },
};

function StatusDot({ status }: { status: TabStatus }) {
  if (status === "none") return null;
  const { className, title } = DOT[status];
  return (
    <span
      title={title}
      aria-label={title}
      className={`ml-auto size-1.5 shrink-0 rounded-full ${className}`}
    />
  );
}

function NavRow({ tab }: { tab: TabDef }) {
  const { data: capabilities } = useCapabilities();
  const status = tabStatus(tab, capabilities);
  return (
    <Link
      to={tab.to}
      activeOptions={{ exact: tab.to === "/ui" }}
      className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-muted-foreground text-sm transition-colors hover:bg-foreground/5 hover:text-foreground"
      activeProps={{ className: "bg-secondary font-medium text-foreground" }}
    >
      <NavIcon name={tab.icon} />
      <span className="truncate">{tab.label}</span>
      <StatusDot status={status} />
    </Link>
  );
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2.5 pb-1 font-medium text-[10.5px] text-muted-foreground/80 uppercase tracking-[0.09em]">
      {children}
    </div>
  );
}

function RootLayout() {
  const { data: capabilities } = useCapabilities();
  const gated = TABS.filter((tab) => tab.capability);
  const ready = gated.filter((tab) => tabStatus(tab, capabilities) === "ready").length;

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <nav className="flex w-[244px] shrink-0 flex-col gap-5 border-border border-r p-3">
        <div className="flex items-center gap-2.5 px-2 pt-1.5 text-primary">
          <NavIcon name="logo" size={20} />
          <h1 className="font-semibold text-base text-foreground tracking-[-0.01em]">
            cinemagraph
          </h1>
        </div>

        <div className="flex flex-col gap-1">
          <GroupLabel>Create</GroupLabel>
          {TABS.filter((tab) => tab.group === "create").map((tab) => (
            <NavRow key={tab.to} tab={tab} />
          ))}
        </div>

        <div className="flex flex-col gap-1">
          <GroupLabel>Assemble</GroupLabel>
          {TABS.filter((tab) => tab.group === "assemble").map((tab) => (
            <NavRow key={tab.to} tab={tab} />
          ))}
        </div>

        <div className="mt-auto flex flex-col gap-2">
          {/* Only worth saying when something is actually off -- a permanent
              "5 of 5 ready" banner is furniture. */}
          {capabilities && ready < gated.length ? (
            <div className="flex flex-col gap-1.5 rounded-lg border border-border p-3">
              <div className="flex items-center gap-2">
                <span className="size-1.5 shrink-0 rounded-full bg-warning" />
                <span className="font-medium text-xs">
                  {ready} of {gated.length} engines ready
                </span>
              </div>
              <Link to="/ui/setup" className="font-medium text-primary text-xs hover:underline">
                Set up engines →
              </Link>
            </div>
          ) : null}
          {TABS.filter((tab) => tab.group === "system").map((tab) => (
            <NavRow key={tab.to} tab={tab} />
          ))}
        </div>
      </nav>

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-4xl px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

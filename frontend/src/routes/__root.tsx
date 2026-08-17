import { Link, Outlet, createRootRoute } from "@tanstack/react-router";

import { useCapabilities } from "@/hooks/useCapabilities";
import { TABS, tabAvailability } from "@/lib/tabs";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  const { data: capabilities } = useCapabilities();

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-3xl px-4 py-8">
        <header className="space-y-1">
          <h1 className="text-xl font-semibold">cinemagraph-tool</h1>
          <p className="text-xs text-muted-foreground">
            A thin client over the same API a script or curl would use — nothing here that the
            underlying routes don't already do themselves.
          </p>
        </header>
        <nav className="mt-6 flex flex-wrap gap-1 border-border border-b">
          {TABS.map((tab) => {
            const { available, configured } = tabAvailability(tab, capabilities);
            // Not configured at all: no link. Configured but down: linked, with
            // a ⚠ marker and a hint banner inside the route itself.
            if (!available && !configured) return null;
            return (
              <Link
                key={tab.to}
                to={tab.to}
                activeOptions={{ exact: tab.to === "/ui" }}
                className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
                activeProps={{ className: "border-primary text-foreground" }}
              >
                {available ? tab.label : `${tab.label} ⚠`}
              </Link>
            );
          })}
        </nav>

        <main className="py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

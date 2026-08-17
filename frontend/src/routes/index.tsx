import { createFileRoute, redirect } from "@tanstack/react-router";

/** The app itself lives under /ui (see lib/tabs.ts for why it's namespaced);
 * "/" is just the door people actually type. */
export const Route = createFileRoute("/")({
  beforeLoad: () => {
    throw redirect({ to: "/ui" });
  },
});

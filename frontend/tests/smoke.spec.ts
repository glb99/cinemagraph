import { expect, test } from "@playwright/test";

// These run against a live backend -- `uv run uvicorn server.app:app --reload`
// (or the docker-compose `core` service) reachable through Vite's dev-server
// proxy. They cover the always-available tabs only: Music/Sound effects/Image
// are hidden unless their optional satellite is configured, so asserting on
// them would make the suite depend on which containers happen to be up.

test("photo tab renders the effect list and the always-available nav", async ({ page }) => {
  await page.goto("/");
  // "/" is a redirect: the app itself is namespaced under /ui so it never
  // shadows an API path (see src/lib/tabs.ts).
  await expect(page).toHaveURL(/\/ui$/);

  await expect(page.getByRole("heading", { name: "cinemagraph" })).toBeVisible();
  for (const tab of ["Photo", "Video", "Assemble", "Library"]) {
    await expect(page.getByRole("link", { name: tab, exact: true })).toBeVisible();
  }

  // Populated from GET /effects, so this doubles as the proof that the
  // generated SDK is actually talking to the backend through the proxy.
  await expect(page.getByTestId("effect-list").getByText("smoke", { exact: true })).toBeVisible({
    timeout: 10_000,
  });
});

test("photo render validates before it submits anything", async ({ page }) => {
  await page.goto("/ui");
  await page.getByRole("button", { name: "Render" }).click();
  await expect(page.getByRole("alert")).toHaveText("Select at least one effect.");
});

test("video tab rejects loop duration combined with gif export", async ({ page }) => {
  await page.goto("/ui/video");

  await page.getByLabel("Video").setInputFiles({
    name: "clip.mp4",
    mimeType: "video/mp4",
    // Never decoded: the conflict below is caught client-side, before any
    // request goes out, so the bytes only have to satisfy the file input.
    buffer: Buffer.from("not a real video"),
  });
  await page.getByLabel("Also export .gif").check();
  await page.getByLabel("Loop duration (s)").fill("3600");
  await page.getByRole("button", { name: "Render" }).click();

  await expect(page.getByRole("alert")).toHaveText(
    "Loop duration isn't compatible with .gif export.",
  );
});

test("library tab lists assets from GET /library", async ({ page }) => {
  await page.goto("/ui/library");

  await expect(page.getByRole("combobox", { name: "Kind" })).toBeVisible();
  // Either the first card or the empty-state line, depending on what's in the
  // library -- both mean the fetch resolved, and exactly one of them is on
  // screen in either case. Asserting on the grid *container* instead would
  // pass only against a populated library: the container always renders, but
  // an empty one has no height and so isn't visible. Found running this
  // against a fresh container.
  const firstCard = page.getByTestId("library-grid").locator("> div").first();
  await expect(firstCard.or(page.getByText("No assets yet."))).toBeVisible({
    timeout: 10_000,
  });
});

test("setup tab reports every optional service and how to enable it", async ({ page }) => {
  await page.goto("/ui/setup");

  await expect(page.getByRole("heading", { name: "Setup" })).toBeVisible();

  // Always-available, unlike the gated tabs -- the whole point is that it
  // works when nothing else is configured, so it's safe to assert on here.
  await expect(page.getByRole("link", { name: "Setup", exact: true })).toBeVisible();

  // Every satellite is listed whether or not it's configured, with the env var
  // that turns it on. Which *state* each is in depends on what's running, so
  // that deliberately isn't asserted -- only that the row and its variable are
  // present, which is what makes the page actionable.
  for (const service of [
    "Semantic masking",
    "Music generation",
    "Sound effect generation",
    "Image generation",
  ]) {
    await expect(page.getByText(service, { exact: true })).toBeVisible({ timeout: 10_000 });
  }
  for (const envVar of [
    "ML_SERVICE_URL",
    "ACESTEP_URL",
    "SOUND_EFFECTS_URL",
    "IMAGE_GENERATION_URL",
  ]) {
    // .first(): each variable appears twice by design when the service is off
    // -- once as the row's own label, and once inside the "start it, then set
    // this" sentence, which has to name it to stand alone.
    await expect(page.getByText(envVar, { exact: true }).first()).toBeVisible();
  }

  // Recheck re-runs the capability probe rather than reading a cached answer,
  // which is the reason this tab exists as more than a static help page.
  await page.getByRole("button", { name: "Recheck" }).click();
  await expect(page.getByRole("button", { name: "Recheck" })).toBeEnabled({ timeout: 10_000 });
});

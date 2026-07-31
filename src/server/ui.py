"""A minimal, self-contained web UI: one HTML page, inline CSS/JS, no build
step, no separate static-file packaging concerns (see the api-to-server move
in docs/DESIGN.md's decision log for why non-.py assets are a real footgun
here -- this sidesteps that entirely by being plain Python source).

Seven tabs: photo rendering, video rendering, music generation, sound-effect
generation, image generation, long-form assembly (sec 5.6), and a library
browser. Photo/video/assemble/library are always available; music, sound
effects, and image generation are feature-gated on GET /capabilities, same
signal the rest of the system already uses to degrade gracefully when an
optional service isn't running -- their tab buttons are hidden entirely
rather than shown-disabled, matching how mask_prompt was already hidden in
the original photo-only version of this page. Per-effect override flags
(--rain-count etc. on the CLI) and video's grade fine-tuning knobs are
intentionally left out of every form here, same as the original photo tab
-- this is a thin client covering the common path, not full parity with
every CLI flag.

The assemble tab is the one exception to "every form here is a single-file-
upload" -- POST /assemble takes lists of library asset ids, not uploads, so
its picker fetches GET /library?kind=generated once, buckets assets by file
extension into clips/music/sound-effects (reusing assetPreviewElement's own
extension classification below), and tracks selection as ordered arrays
(clips/music, where playback order = click order) or an unordered list
(sound effects, mixed together regardless of order) in `assembleState`.

The library tab lists whatever /render and /generate have auto-registered
(see service.py's library_kind wiring) via GET /library, with client-side
kind/tag filtering. asset.original_filename and asset.tags are user-supplied
(an uploaded file's own name, or free-text tags from a manual `library add`)
-- rendered via textContent/createElement throughout, never innerHTML, so
neither can inject markup into the page.
"""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cinemagraph-tool</title>
<style>
  :root { color-scheme: dark light; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 720px; margin: 2rem auto; padding: 0 1rem;
    background: #14161a; color: #e8e8e8;
  }
  h1 { font-size: 1.3rem; font-weight: 600; }
  fieldset { border: 1px solid #333; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
  legend { padding: 0 0.5rem; color: #aaa; font-size: 0.85rem; }
  label { display: inline-flex; align-items: center; gap: 0.3rem; margin: 0.2rem 0.8rem 0.2rem 0; }
  input[type="number"] { width: 5rem; }
  input[type="text"], textarea { width: 100%; box-sizing: border-box; font-family: inherit; }
  textarea { min-height: 4rem; }
  button {
    background: #3a6ff0; color: white; border: none; border-radius: 6px;
    padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer;
  }
  button:disabled { background: #555; cursor: not-allowed; }
  nav { display: flex; gap: 0.4rem; margin-bottom: 1.2rem; border-bottom: 1px solid #333; }
  nav button {
    background: none; color: #888; border: none; border-radius: 0;
    padding: 0.5rem 0.9rem; font-size: 0.95rem; border-bottom: 2px solid transparent;
  }
  nav button.active { color: #e8e8e8; border-bottom-color: #3a6ff0; }
  .status { margin-top: 1rem; font-size: 0.9rem; color: #aaa; }
  .error { color: #ff6b6b; white-space: pre-wrap; }
  video, audio, img.preview { max-width: 100%; margin-top: 1rem; border-radius: 8px; }
  .row { margin-bottom: 0.6rem; }
  .hint { color: #888; font-size: 0.8rem; }
  .config-hint {
    color: #e0a83a; font-size: 0.85rem; background: #2a2410; border: 1px solid #4a3f18;
    border-radius: 6px; padding: 0.6rem 0.8rem; margin-bottom: 0.8rem;
  }
  .tab { display: none; }
  .tab.active { display: block; }
  .library-toolbar { display: flex; gap: 0.6rem; align-items: center; margin-bottom: 1rem; }
  .library-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 1rem; }
  .library-card { border: 1px solid #333; border-radius: 8px; padding: 0.6rem; }
  .library-card img, .library-card video, .library-card audio { max-width: 100%; border-radius: 6px; margin-top: 0; }
  .library-meta { margin: 0.5rem 0; font-size: 0.85rem; }
  .library-card button { padding: 0.3rem 0.7rem; font-size: 0.85rem; background: #555; }
  .asset-picker { border: 1px solid #333; border-radius: 8px; padding: 0.5rem 0.8rem; max-height: 10rem; overflow-y: auto; }
  .asset-picker button {
    display: block; width: 100%; text-align: left; background: none; color: #e8e8e8;
    border: none; border-radius: 4px; padding: 0.35rem 0.4rem; font-size: 0.9rem; margin: 0.1rem 0;
  }
  .asset-picker button:hover { background: #24272e; }
  .asset-picker button:disabled { color: #555; cursor: default; }
  .asset-picker button:disabled:hover { background: none; }
  .selected-chips { margin: 0.5rem 0; display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .chip {
    display: inline-flex; align-items: center; gap: 0.4rem; background: #24272e;
    border-radius: 999px; padding: 0.25rem 0.5rem 0.25rem 0.7rem; font-size: 0.85rem;
  }
  .chip button {
    background: none; color: #aaa; border: none; padding: 0 0.2rem; font-size: 0.9rem; cursor: pointer;
  }
</style>
</head>
<body>
<h1>cinemagraph-tool</h1>
<p class="hint">A thin client over the same API a script or curl would use -- nothing here that
the underlying routes don't already do themselves.</p>

<nav id="nav"></nav>

<!-- Photo -->
<section class="tab active" id="tab-photo">
<form id="photo-form">
  <div class="row">
    <label>Photo <input type="file" id="photo-input" accept="image/*"></label>
  </div>
  <div class="row">
    <p class="hint">...or pick an existing photo/image from the library instead of uploading:</p>
    <div class="asset-picker" id="photo-library-picker">(loading...)</div>
    <div class="selected-chips" id="photo-library-selected"></div>
  </div>
  <fieldset>
    <legend>Effects</legend>
    <div id="photo-effects">(loading...)</div>
  </fieldset>
  <fieldset>
    <legend>Mask (optional -- omit to animate the whole photo)</legend>
    <div class="row">
      <label>Hand-painted mask file <input type="file" id="photo-mask" accept="image/png"></label>
    </div>
    <div class="row" id="photo-mask-prompt-row" style="display:none">
      <label style="display:block">Or describe what to animate (semantic masking)
        <input type="text" id="photo-mask-prompt" placeholder='e.g. "clouds", "sun", "water"'>
      </label>
    </div>
  </fieldset>
  <fieldset>
    <legend>Timing</legend>
    <label>Duration (s) <input type="number" id="photo-duration" value="4" min="0.5" step="0.5"></label>
    <label>FPS <input type="number" id="photo-fps" value="30" min="1"></label>
    <label>Speed <input type="number" id="photo-speed" value="1.0" min="0.1" step="0.1"></label>
    <label>Loop duration (s) <input type="number" id="photo-loop-duration" min="1" step="1" placeholder="e.g. 3600"></label>
    <p class="hint">Stretches the output to this length by repeating the --duration loop, instead of
    rendering unique frames the whole way (e.g. an hour-long ambient loop).</p>
  </fieldset>
  <button type="submit">Render</button>
</form>
<div class="status" id="photo-status"></div>
<div class="error" id="photo-error"></div>
<video id="photo-preview" controls loop style="display:none"></video>
</section>

<!-- Video -->
<section class="tab" id="tab-video">
<form id="video-form">
  <div class="row">
    <label>Video <input type="file" id="video-input" accept="video/*" required></label>
  </div>
  <fieldset>
    <legend>Mask (optional -- omit to auto-detect motion)</legend>
    <label>Hand-painted mask file <input type="file" id="video-mask" accept="image/png"></label>
    <label>Mask threshold <input type="number" id="video-mask-threshold" value="25" min="1"></label>
  </fieldset>
  <fieldset>
    <legend>Loop</legend>
    <label>Still frame index <input type="number" id="video-still-frame" value="0" min="0"></label>
    <label>Blend frames <input type="number" id="video-blend-frames" value="10" min="0"></label>
    <label><input type="checkbox" id="video-auto-trim" checked> Auto-trim to best loop point</label>
  </fieldset>
  <fieldset>
    <legend>Output</legend>
    <label><input type="checkbox" id="video-gif"> Also export .gif</label>
    <label>Loop duration (s) <input type="number" id="video-loop-duration" min="1" step="1" placeholder="e.g. 3600"></label>
    <p class="hint">Stretches the output to this length by repeating the detected loop, instead of
    rendering unique frames the whole way (e.g. an hour-long ambient loop from a few seconds of
    source). Not compatible with .gif export.</p>
  </fieldset>
  <button type="submit">Render</button>
</form>
<div class="status" id="video-status"></div>
<div class="error" id="video-error"></div>
<video id="video-preview" controls loop style="display:none"></video>
</section>

<!-- Music -->
<section class="tab" id="tab-music">
<div class="config-hint" id="music-config-hint" style="display:none"></div>
<form id="music-form">
  <div class="row">
    <label style="display:block">Prompt
      <input type="text" id="music-prompt" placeholder='e.g. "ambient synth pad, slow, sci-fi"'>
    </label>
  </div>
  <div class="row">
    <label style="display:block">Lyrics (optional)
      <textarea id="music-lyrics"></textarea>
    </label>
  </div>
  <label>Duration (s) <input type="number" id="music-duration" value="30" min="5"></label>
  <label><input type="checkbox" id="music-thinking" checked> Thinking mode</label>
  <label><input type="checkbox" id="music-instrumental"> Instrumental (no vocals)</label>
  <div class="row"></div>
  <button type="submit">Generate</button>
</form>
<div class="status" id="music-status"></div>
<div class="error" id="music-error"></div>
<audio id="music-preview" controls style="display:none"></audio>
</section>

<!-- Sound effects -->
<section class="tab" id="tab-sfx">
<div class="config-hint" id="sfx-config-hint" style="display:none"></div>
<form id="sfx-form">
  <div class="row">
    <label style="display:block">Prompt
      <input type="text" id="sfx-prompt" placeholder='e.g. "gentle wind chimes in a light breeze"'>
    </label>
  </div>
  <label>Duration (s) <input type="number" id="sfx-duration" value="10" min="1" max="47"></label>
  <div class="row"></div>
  <button type="submit">Generate</button>
</form>
<div class="status" id="sfx-status"></div>
<div class="error" id="sfx-error"></div>
<audio id="sfx-preview" controls style="display:none"></audio>
</section>

<!-- Image -->
<section class="tab" id="tab-image">
<div class="config-hint" id="image-config-hint" style="display:none"></div>
<form id="image-form">
  <div class="row">
    <label style="display:block">Prompt
      <input type="text" id="image-prompt" placeholder='e.g. "a lofi bedroom at sunset, warm light"'>
    </label>
  </div>
  <div class="row">
    <label style="display:block">Reference image (optional -- guides the result instead of starting from noise)
      <input type="file" id="image-reference" accept="image/*">
    </label>
  </div>
  <label>Strength <input type="number" id="image-strength" value="0.6" min="0" max="1" step="0.05"></label>
  <div class="row" id="image-model-row" style="display:none">
    <label>Model <select id="image-model"></select></label>
  </div>
  <div class="row"></div>
  <button type="submit">Generate</button>
</form>
<div class="status" id="image-status"></div>
<div class="error" id="image-error"></div>
<img id="image-preview" class="preview" style="display:none">
</section>

<!-- Assemble -->
<section class="tab" id="tab-assemble">
<p class="hint">Combines already-generated library assets into one video -- click to add each in
playback order, click again from the selected list to remove. Sound effects (optional) mix
together continuously under the music, order doesn't matter for those.</p>
<div class="row">
  <strong>Video clips (in order)</strong>
  <div class="asset-picker" id="assemble-clip-picker"></div>
  <div class="selected-chips" id="assemble-clip-selected"></div>
</div>
<div class="row">
  <strong>Music tracks (in order)</strong>
  <div class="asset-picker" id="assemble-music-picker"></div>
  <div class="selected-chips" id="assemble-music-selected"></div>
</div>
<div class="row">
  <strong>Sound effects (optional)</strong>
  <div class="asset-picker" id="assemble-sfx-picker"></div>
  <div class="selected-chips" id="assemble-sfx-selected"></div>
</div>
<div class="row">
  <label>Video crossfade (s) <input type="number" id="assemble-video-crossfade" value="1.0" step="0.1" min="0"></label>
  <label>Music crossfade (s) <input type="number" id="assemble-music-crossfade" value="5.0" step="0.1" min="0"></label>
  <label>Music edge fade (s) <input type="number" id="assemble-music-edge-fade" value="2.0" step="0.1" min="0"></label>
  <label>Music gap (s) <input type="number" id="assemble-music-gap" value="0" step="0.1" min="0"></label>
</div>
<p class="hint">Music gap: a silent pause between songs instead of crossfading them (0 = crossfade
as usual). Sound effects keep playing continuously through the gap -- only the music pauses.</p>
<button type="button" id="assemble-refresh">Refresh assets</button>
<button type="button" id="assemble-submit">Assemble</button>
<div class="status" id="assemble-status"></div>
<div class="error" id="assemble-error"></div>
<video id="assemble-preview" class="preview" controls style="display:none"></video>
</section>

<!-- Library -->
<section class="tab" id="tab-library">
<div class="library-toolbar">
  <label>Kind
    <select id="library-kind-filter">
      <option value="">all</option>
      <option value="reference">reference</option>
      <option value="source">source</option>
      <option value="generated">generated</option>
    </select>
  </label>
  <label>Tag <input type="text" id="library-tag-filter" placeholder="e.g. photo"></label>
  <button type="button" id="library-refresh">Refresh</button>
</div>
<div class="error" id="library-error"></div>
<div class="library-grid" id="library-list">(loading...)</div>
</section>

<script>
const TABS = [
  { id: "photo", label: "Photo", always: true },
  { id: "video", label: "Video", always: true },
  {
    id: "music", label: "Music", capability: "music_generation",
    startCommand: "docker compose --profile audio up acestep",
  },
  {
    id: "sfx", label: "Sound effects", capability: "sound_effect_generation",
    startCommand: "docker compose --profile audio up sound-effects",
  },
  {
    id: "image", label: "Image", capability: "image_generation",
    startCommand: "docker compose --profile image up image-generation",
  },
  { id: "assemble", label: "Assemble", always: true },
  { id: "library", label: "Library", always: true },
];

function showTab(id) {
  for (const tab of TABS) {
    document.getElementById(`tab-${tab.id}`).classList.toggle("active", tab.id === id);
  }
  for (const btn of document.querySelectorAll("#nav button")) {
    btn.classList.toggle("active", btn.dataset.tab === id);
  }
  if (id === "library") loadLibrary();
  if (id === "assemble") loadAssembleAssets();
}

async function loadCapabilities() {
  const caps = await (await fetch("/capabilities")).json();
  if (caps.semantic_mask) {
    document.getElementById("photo-mask-prompt-row").style.display = "block";
  }

  // Model choice only makes sense once a second adapter (e.g. Gemini) is
  // actually registered -- with just "sdxl", the dropdown would be a choice
  // of one, which is no choice at all (see docs/DESIGN.md sec 3.6).
  const models = caps.image_generation_models || [];
  if (models.length > 1) {
    const row = document.getElementById("image-model-row");
    const select = document.getElementById("image-model");
    select.innerHTML = "";
    for (const name of models) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
    row.style.display = "block";
  }

  const nav = document.getElementById("nav");
  let firstVisible = null;
  for (const tab of TABS) {
    const available = tab.always || !!caps[tab.capability];
    // Configured-but-unreachable (e.g. IMAGE_GENERATION_URL is set but the
    // container isn't running right now) still shows the tab, with a hint
    // banner inside it -- distinct from "not configured at all", which
    // stays hidden (nothing to hint about without redeploying). See
    // app.py's /capabilities docstring for the configured/available split.
    const configured = !tab.always && !!(caps.configured && caps.configured[tab.capability]);
    if (!available && !configured) continue;
    if (firstVisible === null && available) firstVisible = tab.id;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = available ? tab.label : `${tab.label} ⚠`;
    btn.dataset.tab = tab.id;
    btn.addEventListener("click", () => showTab(tab.id));
    nav.appendChild(btn);

    if (!available && configured && tab.startCommand) {
      const hintEl = document.getElementById(`${tab.id}-config-hint`);
      if (hintEl) {
        hintEl.textContent =
          `${tab.label} is configured but not reachable right now -- ` +
          `its container probably isn't running. Start it with: ${tab.startCommand}`;
        hintEl.style.display = "block";
      }
    }
  }
  showTab(firstVisible || "photo");
}

async function loadEffects() {
  const data = await (await fetch("/effects")).json();
  const container = document.getElementById("photo-effects");
  container.innerHTML = "";
  for (const name of data.effects) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "effect";
    input.value = name;
    label.appendChild(input);
    label.appendChild(document.createTextNode(name));
    container.appendChild(label);
  }
}

/** Every generated asset's original_filename is literally "output.mp4"/
 * "output.mp3"/"output.png" -- that's the fixed filename every job writes
 * before it's hashed into the library, so it's the same for every
 * generated asset and useless as a picker label on its own. Prefers the
 * asset's own provenance.prompt (what you actually typed, if this was a
 * generation job) when present; falls back to filename + when it was
 * added, since even non-generated assets need *something* to tell two
 * same-named entries apart. */
function assetPickerLabel(asset) {
  const shortId = asset.id.slice(0, 8);
  const prompt = asset.provenance && asset.provenance.prompt;
  if (prompt) {
    const trimmed = prompt.length > 40 ? prompt.slice(0, 40) + "…" : prompt;
    return `${trimmed} (${shortId})`;
  }
  const when = new Date(asset.added_at).toLocaleString();
  return `${asset.original_filename} — ${when} (${shortId})`;
}

/** Photo tab's input is either a fresh upload or a library asset -- exactly
 * one, same "pick one or the other" pattern already used for mask vs
 * mask_prompt on this tab. Selecting a library asset clears any chosen
 * upload file and vice versa (wired below), rather than allowing both and
 * discovering the conflict only at submit time. */
let photoLibraryAssetId = null;

async function loadPhotoLibraryAssets() {
  const assets = await (await fetch("/library")).json();
  const imageExts = ["jpg", "jpeg", "png", "webp"];
  const container = document.getElementById("photo-library-picker");
  container.innerHTML = "";
  const imageAssets = assets.filter(
    a => imageExts.includes((a.original_filename.split(".").pop() || "").toLowerCase())
  );
  if (imageAssets.length === 0) {
    container.textContent = "(no images in the library yet)";
    return;
  }
  for (const asset of imageAssets) {
    const btn = document.createElement("button");
    btn.type = "button";
    const selected = photoLibraryAssetId === asset.id;
    btn.textContent = (selected ? "✓ " : "+ ") + assetPickerLabel(asset);
    btn.addEventListener("click", () => {
      photoLibraryAssetId = selected ? null : asset.id;
      document.getElementById("photo-input").value = "";
      renderPhotoLibrarySelection(asset);
      loadPhotoLibraryAssets();
    });
    container.appendChild(btn);
  }
}

function renderPhotoLibrarySelection(asset) {
  const container = document.getElementById("photo-library-selected");
  container.innerHTML = "";
  if (!photoLibraryAssetId) return;
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.appendChild(document.createTextNode(assetPickerLabel(asset)));
  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.textContent = "×";
  removeBtn.addEventListener("click", () => {
    photoLibraryAssetId = null;
    loadPhotoLibraryAssets();
    container.innerHTML = "";
  });
  chip.appendChild(removeBtn);
  container.appendChild(chip);
}

document.getElementById("photo-input").addEventListener("change", () => {
  if (document.getElementById("photo-input").files[0] && photoLibraryAssetId) {
    photoLibraryAssetId = null;
    document.getElementById("photo-library-selected").innerHTML = "";
    loadPhotoLibraryAssets();
  }
});

function selectedEffects() {
  return Array.from(document.querySelectorAll('input[name="effect"]:checked')).map(i => i.value);
}

/** Picks a preview element by the asset's own file extension -- kind alone
 * doesn't disambiguate (a "generated" video and a "generated" sound effect
 * both need different tags <video>/<audio>). Falls back to a plain download
 * link for anything else (masks are .png, which img already covers). */
function assetPreviewElement(asset) {
  const url = `/library/${asset.id}/file`;
  const ext = (asset.original_filename.split(".").pop() || "").toLowerCase();
  if (["jpg", "jpeg", "png", "webp", "gif"].includes(ext)) {
    const img = document.createElement("img");
    img.src = url;
    return img;
  }
  if (["mp4", "webm", "mov"].includes(ext)) {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.loop = true;
    return video;
  }
  if (["mp3", "wav", "ogg"].includes(ext)) {
    const audio = document.createElement("audio");
    audio.src = url;
    audio.controls = true;
    return audio;
  }
  const link = document.createElement("a");
  link.href = url;
  link.textContent = "Download";
  return link;
}

/** original_filename/tags are user-supplied (an upload's own name, or
 * free-text tags from a manual `library add`), so every dynamic value here
 * goes through textContent/createElement, never innerHTML -- see this
 * module's docstring. */
function assetCard(asset) {
  const card = document.createElement("div");
  card.className = "library-card";
  card.appendChild(assetPreviewElement(asset));

  const meta = document.createElement("div");
  meta.className = "library-meta";

  const kindLine = document.createElement("div");
  const kindLabel = document.createElement("strong");
  kindLabel.textContent = asset.kind;
  kindLine.appendChild(kindLabel);
  kindLine.appendChild(document.createTextNode(" · " + asset.original_filename));
  meta.appendChild(kindLine);

  const dateLine = document.createElement("div");
  dateLine.className = "hint";
  dateLine.textContent = new Date(asset.added_at).toLocaleString();
  meta.appendChild(dateLine);

  if (asset.tags.length > 0) {
    const tagLine = document.createElement("div");
    tagLine.className = "hint";
    tagLine.textContent = "tags: " + asset.tags.join(", ");
    meta.appendChild(tagLine);
  }
  card.appendChild(meta);

  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.textContent = "Delete";
  delBtn.addEventListener("click", async () => {
    if (!confirm(`Delete "${asset.original_filename}"? This cannot be undone.`)) return;
    await fetch(`/library/${asset.id}`, { method: "DELETE" });
    loadLibrary();
  });
  card.appendChild(delBtn);

  return card;
}

async function loadLibrary() {
  const listEl = document.getElementById("library-list");
  const errorEl = document.getElementById("library-error");
  errorEl.textContent = "";
  listEl.textContent = "Loading...";

  const kind = document.getElementById("library-kind-filter").value;
  const tag = document.getElementById("library-tag-filter").value.trim();
  const params = new URLSearchParams();
  if (kind) params.set("kind", kind);
  if (tag) params.set("tag", tag);

  try {
    const res = await fetch(`/library?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const assets = await res.json();

    listEl.innerHTML = "";
    if (assets.length === 0) {
      listEl.textContent = "No assets yet.";
      return;
    }
    for (const asset of assets) listEl.appendChild(assetCard(asset));
  } catch (err) {
    listEl.textContent = "";
    errorEl.textContent = err.message;
  }
}

/** Shared polling loop -- every /generate/* and /render/* route returns the
 * same {job_id} shape and is checked via the same GET /jobs/{id} contract,
 * so one implementation covers all four tabs. `onDone` wires the result
 * into whichever <video>/<audio> element belongs to that tab. */
async function pollJob(jobId, { statusEl, errorEl, onDone }) {
  for (;;) {
    const res = await fetch(`/jobs/${jobId}`);
    const job = await res.json();
    statusEl.textContent = `Job ${jobId}: ${job.status}`;
    if (job.status === "done") {
      onDone(`/jobs/${jobId}/file`);
      return;
    }
    if (job.status === "error") {
      errorEl.textContent = job.error;
      return;
    }
    await new Promise(r => setTimeout(r, 1500));
  }
}

function wireForm(formId, { buildForm, endpoint, statusId, errorId, previewId }) {
  const form = document.getElementById(formId);
  const statusEl = document.getElementById(statusId);
  const errorEl = document.getElementById(errorId);
  const previewEl = document.getElementById(previewId);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    previewEl.style.display = "none";
    const submitBtn = form.querySelector("button");
    submitBtn.disabled = true;

    try {
      const body = buildForm();
      if (body === null) return; // buildForm already set errorEl

      const res = await fetch(endpoint, { method: "POST", body });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const { job_id } = await res.json();
      statusEl.textContent = `Job ${job_id}: submitted`;
      await pollJob(job_id, {
        statusEl, errorEl,
        onDone: (url) => { previewEl.src = url; previewEl.style.display = "block"; },
      });
    } catch (err) {
      errorEl.textContent = err.message;
    } finally {
      submitBtn.disabled = false;
    }
  });
}

wireForm("photo-form", {
  endpoint: "/render/photo",
  statusId: "photo-status", errorId: "photo-error", previewId: "photo-preview",
  buildForm: () => {
    const effects = selectedEffects();
    if (effects.length === 0) {
      document.getElementById("photo-error").textContent = "Select at least one effect.";
      return null;
    }
    const photoFile = document.getElementById("photo-input").files[0];
    if (!photoFile && !photoLibraryAssetId) {
      document.getElementById("photo-error").textContent = "Upload a photo or pick one from the library.";
      return null;
    }
    const maskFile = document.getElementById("photo-mask").files[0];
    const maskPrompt = document.getElementById("photo-mask-prompt").value.trim();
    if (maskFile && maskPrompt) {
      document.getElementById("photo-error").textContent = "Supply either a mask file or a mask prompt, not both.";
      return null;
    }
    const form = new FormData();
    if (photoFile) form.append("input_file", photoFile);
    else form.append("input_asset_id", photoLibraryAssetId);
    for (const eff of effects) form.append("effect", eff);
    if (maskFile) form.append("mask", maskFile);
    if (maskPrompt) form.append("mask_prompt", maskPrompt);
    form.append("duration", document.getElementById("photo-duration").value);
    form.append("fps", document.getElementById("photo-fps").value);
    form.append("speed", document.getElementById("photo-speed").value);
    const loopDuration = document.getElementById("photo-loop-duration").value;
    if (loopDuration) form.append("loop_duration", loopDuration);
    return form;
  },
});

wireForm("video-form", {
  endpoint: "/render/video",
  statusId: "video-status", errorId: "video-error", previewId: "video-preview",
  buildForm: () => {
    const loopDuration = document.getElementById("video-loop-duration").value;
    const alsoGif = document.getElementById("video-gif").checked;
    if (loopDuration && alsoGif) {
      document.getElementById("video-error").textContent = "Loop duration isn't compatible with .gif export.";
      return null;
    }
    const form = new FormData();
    form.append("input_file", document.getElementById("video-input").files[0]);
    const maskFile = document.getElementById("video-mask").files[0];
    if (maskFile) form.append("mask", maskFile);
    form.append("mask_threshold", document.getElementById("video-mask-threshold").value);
    form.append("still_frame_index", document.getElementById("video-still-frame").value);
    form.append("blend_frames", document.getElementById("video-blend-frames").value);
    form.append("auto_trim", document.getElementById("video-auto-trim").checked);
    form.append("also_gif", alsoGif);
    if (loopDuration) form.append("loop_duration", loopDuration);
    return form;
  },
});

wireForm("music-form", {
  endpoint: "/generate/music",
  statusId: "music-status", errorId: "music-error", previewId: "music-preview",
  buildForm: () => {
    const prompt = document.getElementById("music-prompt").value.trim();
    if (!prompt) {
      document.getElementById("music-error").textContent = "Prompt is required.";
      return null;
    }
    const form = new FormData();
    form.append("prompt", prompt);
    form.append("lyrics", document.getElementById("music-lyrics").value);
    form.append("duration", document.getElementById("music-duration").value);
    form.append("thinking", document.getElementById("music-thinking").checked);
    form.append("instrumental", document.getElementById("music-instrumental").checked);
    return form;
  },
});

wireForm("sfx-form", {
  endpoint: "/generate/sound-effect",
  statusId: "sfx-status", errorId: "sfx-error", previewId: "sfx-preview",
  buildForm: () => {
    const prompt = document.getElementById("sfx-prompt").value.trim();
    if (!prompt) {
      document.getElementById("sfx-error").textContent = "Prompt is required.";
      return null;
    }
    const form = new FormData();
    form.append("prompt", prompt);
    form.append("duration", document.getElementById("sfx-duration").value);
    return form;
  },
});

wireForm("image-form", {
  endpoint: "/generate/image",
  statusId: "image-status", errorId: "image-error", previewId: "image-preview",
  buildForm: () => {
    const prompt = document.getElementById("image-prompt").value.trim();
    if (!prompt) {
      document.getElementById("image-error").textContent = "Prompt is required.";
      return null;
    }
    const form = new FormData();
    form.append("prompt", prompt);
    const referenceFile = document.getElementById("image-reference").files[0];
    if (referenceFile) {
      form.append("reference_image", referenceFile);
      form.append("strength", document.getElementById("image-strength").value);
    }
    const modelSelect = document.getElementById("image-model");
    if (modelSelect.value) {
      form.append("model", modelSelect.value);
    }
    return form;
  },
});

/** Assemble tab state: clips/music are ordered arrays (playback order =
 * click order, matching POST /assemble's own list-in-order contract);
 * sound effects are an unordered Set (mixed together, order doesn't
 * matter). Asset objects are cached by id so chip labels and re-renders
 * don't need another fetch. */
const assembleState = { clips: [], music: [], sfx: [], assetsById: {} };

/** Buckets kind=generated library assets by file extension (reusing the
 * same classification assetPreviewElement already uses for previews) --
 * video extensions become clip candidates, audio extensions become music
 * candidates, except those tagged "sound-effect" which become sfx
 * candidates instead. A generated asset with neither extension (e.g. a
 * mask PNG someone tagged "generated") is simply not offered anywhere. */
async function loadAssembleAssets() {
  const assets = await (await fetch("/library?kind=generated")).json();
  const videoExts = ["mp4", "webm", "mov"];
  const audioExts = ["mp3", "wav", "ogg"];
  const clipAssets = [], musicAssets = [], sfxAssets = [];
  for (const asset of assets) {
    assembleState.assetsById[asset.id] = asset;
    const ext = (asset.original_filename.split(".").pop() || "").toLowerCase();
    if (videoExts.includes(ext)) clipAssets.push(asset);
    else if (audioExts.includes(ext)) {
      (asset.tags.includes("sound-effect") ? sfxAssets : musicAssets).push(asset);
    }
  }
  renderAssemblePicker("assemble-clip-picker", clipAssets, "clips", false);
  renderAssemblePicker("assemble-music-picker", musicAssets, "music", false);
  renderAssemblePicker("assemble-sfx-picker", sfxAssets, "sfx", true);
  renderAssembleChips();
}

function renderAssemblePicker(containerId, assets, bucket, allowMultiple) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  if (assets.length === 0) {
    container.textContent = "(none available)";
    return;
  }
  for (const asset of assets) {
    const btn = document.createElement("button");
    btn.type = "button";
    const selected = assembleState[bucket].includes(asset.id);
    // allowMultiple (sound effects): every click toggles this one asset in
    // or out. Ordered buckets (clips/music): once selected, the button
    // disables -- removing happens via the chip's own × below, since
    // clicking here again wouldn't disambiguate *which* occurrence to drop
    // if the same asset were ever added twice.
    btn.textContent = (selected ? "✓ " : "+ ") + assetPickerLabel(asset);
    btn.disabled = selected && !allowMultiple;
    btn.addEventListener("click", () => {
      if (allowMultiple && selected) {
        assembleState[bucket].splice(assembleState[bucket].indexOf(asset.id), 1);
      } else {
        assembleState[bucket].push(asset.id);
      }
      renderAssemblePicker(containerId, assets, bucket, allowMultiple);
      renderAssembleChips();
    });
    container.appendChild(btn);
  }
}

function renderAssembleChips() {
  for (const bucket of ["clips", "music", "sfx"]) {
    const container = document.getElementById(`assemble-${bucket === "clips" ? "clip" : bucket}-selected`);
    container.innerHTML = "";
    assembleState[bucket].forEach((assetId, index) => {
      const asset = assembleState.assetsById[assetId];
      const chip = document.createElement("span");
      chip.className = "chip";
      const label = bucket === "sfx" ? assetPickerLabel(asset) : `${index + 1}. ${assetPickerLabel(asset)}`;
      chip.appendChild(document.createTextNode(label));
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.textContent = "×";
      removeBtn.addEventListener("click", () => {
        assembleState[bucket].splice(assembleState[bucket].indexOf(assetId), 1);
        loadAssembleAssets();
      });
      chip.appendChild(removeBtn);
      container.appendChild(chip);
    });
  }
}

document.getElementById("assemble-refresh").addEventListener("click", loadAssembleAssets);
document.getElementById("assemble-submit").addEventListener("click", async () => {
  const statusEl = document.getElementById("assemble-status");
  const errorEl = document.getElementById("assemble-error");
  const previewEl = document.getElementById("assemble-preview");
  errorEl.textContent = "";
  previewEl.style.display = "none";

  if (assembleState.clips.length === 0 || assembleState.music.length === 0) {
    errorEl.textContent = "Pick at least one clip and one music track.";
    return;
  }

  const params = new URLSearchParams();
  for (const id of assembleState.clips) params.append("clip_asset_ids", id);
  for (const id of assembleState.music) params.append("music_asset_ids", id);
  for (const id of assembleState.sfx) params.append("sound_effect_asset_ids", id);
  params.append("video_crossfade_duration", document.getElementById("assemble-video-crossfade").value);
  params.append("music_crossfade_duration", document.getElementById("assemble-music-crossfade").value);
  params.append("music_edge_fade_duration", document.getElementById("assemble-music-edge-fade").value);
  params.append("music_gap_duration", document.getElementById("assemble-music-gap").value);

  const submitBtn = document.getElementById("assemble-submit");
  submitBtn.disabled = true;
  try {
    const res = await fetch("/assemble", { method: "POST", body: params });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    const { job_id } = await res.json();
    statusEl.textContent = `Job ${job_id}: submitted`;
    await pollJob(job_id, {
      statusEl, errorEl,
      onDone: (url) => { previewEl.src = url; previewEl.style.display = "block"; },
    });
  } catch (err) {
    errorEl.textContent = err.message;
  } finally {
    submitBtn.disabled = false;
  }
});

document.getElementById("library-refresh").addEventListener("click", loadLibrary);
document.getElementById("library-kind-filter").addEventListener("change", loadLibrary);
document.getElementById("library-tag-filter").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); loadLibrary(); }
});

loadCapabilities();
loadEffects();
loadPhotoLibraryAssets();
</script>
</body>
</html>
"""

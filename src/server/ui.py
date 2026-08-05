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
  .asset-picker { border: 1px solid #333; border-radius: 8px; padding: 0.5rem 0.8rem; max-height: 20rem; overflow-y: auto; }
  .picker-row { display: flex; align-items: center; gap: 0.6rem; padding: 0.35rem 0.3rem; border-radius: 6px; }
  .picker-row:hover { background: #24272e; }
  .picker-row.selected { background: #1d2b1d; }
  .picker-row img, .picker-row video { border-radius: 4px; margin: 0; flex-shrink: 0; object-fit: cover; }
  .picker-row img { width: 56px; height: 56px; }
  .picker-row video { width: 100px; height: 56px; }
  .picker-row audio { flex: 1 1 auto; min-width: 0; height: 32px; margin: 0; }
  .picker-row-label {
    flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; font-size: 0.9rem;
  }
  .picker-row button { flex-shrink: 0; padding: 0.3rem 0.7rem; font-size: 0.85rem; width: auto; }
  .picker-row button:disabled { background: #555; cursor: default; }
  .selected-chips { margin: 0.5rem 0; display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .chip {
    display: inline-flex; align-items: center; gap: 0.4rem; background: #24272e;
    border-radius: 999px; padding: 0.25rem 0.5rem 0.25rem 0.7rem; font-size: 0.85rem;
  }
  .chip button {
    background: none; color: #aaa; border: none; padding: 0 0.2rem; font-size: 0.9rem; cursor: pointer;
  }
  .waveform {
    display: block; width: 100%; height: 80px; background: #1a1c20;
    border: 1px solid #333; border-radius: 6px; cursor: crosshair; margin-bottom: 0.5rem;
  }
  .save-to-library-btn { background: #2d8a4e; }
  .project-picker { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.5rem; flex-wrap: wrap; }
  .project-picker select { width: auto; }
  .project-picker input[type="text"] { width: 10rem; display: none; }
  .library-project-row { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 1rem; flex-wrap: wrap; }
  .library-project-row select { width: auto; }
  .library-project-row input[type="text"] { width: 10rem; }
  .library-project-row button { padding: 0.4rem 0.8rem; font-size: 0.85rem; }
  .asset-project-row { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.4rem; }
  .asset-project-row select { width: auto; font-size: 0.8rem; }
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
  <div class="row">
    <label>Task type
      <select id="music-task-type">
        <option value="text2music">Text to music</option>
        <option value="cover">Cover (remix an existing song)</option>
        <option value="repaint">Repaint (regenerate a section)</option>
      </select>
    </label>
  </div>
  <div class="row" id="music-source-row" style="display:none">
    <p class="hint">Song to remix, from the library:</p>
    <div class="asset-picker" id="music-source-picker">(loading...)</div>
    <div class="selected-chips" id="music-source-selected"></div>
  </div>
  <div class="row" id="music-cover-strength-row" style="display:none">
    <label>Cover strength <input type="number" id="music-cover-strength" value="1.0" min="0" max="1" step="0.05"></label>
  </div>
  <div class="row" id="music-repaint-row" style="display:none">
    <p class="hint">Drag on the waveform to select the section to repaint (or type the times below):</p>
    <canvas id="music-repaint-waveform" width="680" height="80" class="waveform"></canvas>
    <label>Start (s) <input type="number" id="music-repaint-start" value="0" min="0" step="0.1"></label>
    <label>End (s, -1 = to end) <input type="number" id="music-repaint-end" value="-1" step="0.1"></label>
    <button type="button" id="music-repaint-preview">▶ Preview selection</button>
  </div>
  <div class="row">
    <p class="hint">Optional: reference audio for style transfer (independent of task type):</p>
    <div class="asset-picker" id="music-reference-picker">(loading...)</div>
    <div class="selected-chips" id="music-reference-selected"></div>
  </div>
  <div class="row" id="music-model-row" style="display:none">
    <label>Model <select id="music-model"></select></label>
  </div>
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
  <label>Project <select id="assemble-project-filter"><option value="">all</option></select></label>
  <span class="hint">Narrows the pickers below to one project's assets.</span>
</div>
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
  <label>Project <select id="library-project-filter"><option value="">all</option></select></label>
  <button type="button" id="library-refresh">Refresh</button>
</div>
<div class="library-project-row">
  <strong>Manage projects:</strong>
  <select id="library-project-manage"><option value="">(pick a project)</option></select>
  <span class="hint">rename to</span>
  <input type="text" id="library-project-rename-to" placeholder="new name">
  <button type="button" id="library-project-rename">Rename</button>
  <button type="button" id="library-project-delete">Delete</button>
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

  // Cached (not just populated inline like image's dropdown above) since
  // the Music tab's model list has to be re-filtered later, whenever the
  // task type or reference-audio selection changes -- see
  // updateMusicFormForTaskType(), defined alongside the remix pickers.
  musicGenerationModels = caps.music_generation_models || [];
  musicRemixModels = caps.music_remix_models || [];
  updateMusicFormForTaskType();

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

async function fetchProjects() {
  return (await fetch("/projects")).json();
}

const NEW_PROJECT_VALUE = "__new__";

/** Populates a <select> with "No project", every existing project name, and
 * a trailing "+ New project..." option -- shared by every project picker
 * (Library card, Library/Assemble toolbar, the save-flow's own picker)
 * instead of duplicating this per instance. Pair with wireNewProjectInput
 * (shows/hides a text <input> when "+ New project..." is picked) and
 * selectedProject (reads the effective choice back out at submit time). */
function populateProjectSelect(selectEl, projects, currentValue) {
  selectEl.innerHTML = "";
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = "No project";
  selectEl.appendChild(noneOpt);
  for (const name of projects) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    selectEl.appendChild(opt);
  }
  const newOpt = document.createElement("option");
  newOpt.value = NEW_PROJECT_VALUE;
  newOpt.textContent = "+ New project…";
  selectEl.appendChild(newOpt);
  selectEl.value = currentValue || "";
}

function wireNewProjectInput(selectEl, inputEl) {
  const sync = () => {
    inputEl.style.display = selectEl.value === NEW_PROJECT_VALUE ? "inline-block" : "none";
  };
  selectEl.addEventListener("change", sync);
  sync();
}

/** "" (no project) unless "+ New project..." is selected, in which case the
 * paired input's trimmed value is used (still "" if left blank -- callers
 * treat "" as "no project" either way, so a blank new-project name is a
 * harmless no-op rather than a validation error). */
function selectedProject(selectEl, inputEl) {
  return selectEl.value === NEW_PROJECT_VALUE ? inputEl.value.trim() : selectEl.value;
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
    const selected = photoLibraryAssetId === asset.id;
    container.appendChild(assetPickerRow(asset, {
      selected,
      onToggle: () => {
        photoLibraryAssetId = selected ? null : asset.id;
        document.getElementById("photo-input").value = "";
        renderPhotoLibrarySelection(asset);
        loadPhotoLibraryAssets();
      },
    }));
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

/** Music tab's remix inputs -- same "pick a library asset, removable chip"
 * pattern as the Photo tab's picker above (loadPhotoLibraryAssets/
 * renderPhotoLibrarySelection), filtered to audio instead of image, and
 * duplicated (not factored into a shared helper) twice more: one picker for
 * the song being remixed (cover/repaint), one for an optional reference
 * track (style transfer, independent of task type). */
let musicSourceLibraryAssetId = null;
let musicReferenceLibraryAssetId = null;
const AUDIO_EXTS = ["mp3", "wav", "flac", "ogg", "m4a"];

async function loadMusicSourceLibraryAssets() {
  const assets = await (await fetch("/library")).json();
  const container = document.getElementById("music-source-picker");
  container.innerHTML = "";
  const audioAssets = assets.filter(
    a => AUDIO_EXTS.includes((a.original_filename.split(".").pop() || "").toLowerCase())
  );
  if (audioAssets.length === 0) {
    container.textContent = "(no songs in the library yet)";
    return;
  }
  for (const asset of audioAssets) {
    const selected = musicSourceLibraryAssetId === asset.id;
    container.appendChild(assetPickerRow(asset, {
      selected,
      onToggle: () => {
        musicSourceLibraryAssetId = selected ? null : asset.id;
        renderMusicSourceLibrarySelection(asset);
        loadMusicSourceLibraryAssets();
        updateMusicFormForTaskType();
      },
    }));
  }
}

function renderMusicSourceLibrarySelection(asset) {
  const container = document.getElementById("music-source-selected");
  container.innerHTML = "";
  if (!musicSourceLibraryAssetId) return;
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.appendChild(document.createTextNode(assetPickerLabel(asset)));
  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.textContent = "×";
  removeBtn.addEventListener("click", () => {
    musicSourceLibraryAssetId = null;
    loadMusicSourceLibraryAssets();
    container.innerHTML = "";
  });
  chip.appendChild(removeBtn);
  container.appendChild(chip);
}

/** Repaint's start/end fields are drag-selectable on a waveform instead of
 * typed blind -- canvas + the browser's native Web Audio API
 * (AudioContext.decodeAudioData), no charting library, matching this file's
 * own "single self-contained page, no build step" constraint (see module
 * docstring -- non-.py assets already caused one real packaging bug in this
 * project's history, documented in docs/DESIGN.md's decision log). Peaks
 * (min/max per pixel column, the standard lightweight waveform technique)
 * are computed once per loaded asset and cached, so every redraw triggered
 * by a drag frame or a manual number-input edit is just cheap canvas
 * fillRect calls, not a re-scan of the raw PCM data. */
let repaintWaveformAssetId = null;  // which asset's buffer/peaks are currently loaded
let repaintAudioBuffer = null;      // decoded Web Audio buffer -- source for both peaks and preview playback
let repaintAudioCtx = null;         // created lazily on first use, standard practice for AudioContext
let repaintPeaks = null;            // cached [min, max] pairs, one per canvas pixel column

async function loadRepaintWaveform(assetId) {
  if (!assetId || assetId === repaintWaveformAssetId) return;
  try {
    const resp = await fetch(`/library/${assetId}/file`);
    const arrayBuffer = await resp.arrayBuffer();
    repaintAudioCtx = repaintAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
    repaintAudioBuffer = await repaintAudioCtx.decodeAudioData(arrayBuffer);
    repaintWaveformAssetId = assetId;
    computeRepaintPeaks();
    drawRepaintWaveform();
  } catch (err) {
    document.getElementById("music-error").textContent = `Couldn't load waveform: ${err.message}`;
  }
}

function computeRepaintPeaks() {
  const canvas = document.getElementById("music-repaint-waveform");
  const data = repaintAudioBuffer.getChannelData(0);
  const samplesPerPixel = Math.max(1, Math.floor(data.length / canvas.width));
  repaintPeaks = [];
  for (let x = 0; x < canvas.width; x++) {
    let min = 0, max = 0;
    const start = x * samplesPerPixel;
    for (let i = 0; i < samplesPerPixel; i++) {
      const v = data[start + i] || 0;
      if (v < min) min = v;
      if (v > max) max = v;
    }
    repaintPeaks.push([min, max]);
  }
}

/** Full redraw (bars from the cached peaks, then the selection highlight) --
 * cheap enough (canvas has no persistent "layers" to update incrementally)
 * to call on every drag-move frame and every manual start/end field edit,
 * since it never touches the raw PCM data after computeRepaintPeaks()'s own
 * one-time pass. */
function drawRepaintWaveform() {
  const canvas = document.getElementById("music-repaint-waveform");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!repaintPeaks || !repaintAudioBuffer) return;

  const mid = canvas.height / 2;
  ctx.fillStyle = "#3a6ff0";
  for (let x = 0; x < repaintPeaks.length; x++) {
    const [min, max] = repaintPeaks[x];
    ctx.fillRect(x, mid + min * mid, 1, Math.max(1, (max - min) * mid));
  }

  const duration = repaintAudioBuffer.duration;
  const startSec = parseFloat(document.getElementById("music-repaint-start").value) || 0;
  const endRaw = parseFloat(document.getElementById("music-repaint-end").value);
  const endSec = (endRaw < 0 || isNaN(endRaw)) ? duration : endRaw;
  const x1 = Math.max(0, Math.min(canvas.width, (startSec / duration) * canvas.width));
  const x2 = Math.max(0, Math.min(canvas.width, (endSec / duration) * canvas.width));
  ctx.fillStyle = "rgba(58, 111, 240, 0.35)";
  ctx.fillRect(x1, 0, x2 - x1, canvas.height);
}

let repaintDragStartX = null;

function updateRepaintSelectionFromPixels(pixelX1, pixelX2) {
  const canvas = document.getElementById("music-repaint-waveform");
  // e.offsetX (the caller's pixelX1/pixelX2) is reported relative to the
  // canvas's *rendered* CSS box (stretched to 100% width by .waveform),
  // which is NOT the same as canvas.width (the fixed 680 internal pixel
  // buffer the drawing code and computeRepaintPeaks() use) -- confirmed as
  // a real mismatch during manual verification (rendered ~722px vs. a 680px
  // buffer at typical widths). Using getBoundingClientRect().width here
  // keeps the fraction-of-track math correct regardless of how wide the
  // canvas actually renders; drawRepaintWaveform()'s own fillRect calls
  // stay in buffer-pixel space, which canvas 2D drawing always uses
  // natively, so no equivalent fix is needed there.
  const renderedWidth = canvas.getBoundingClientRect().width;
  const duration = repaintAudioBuffer.duration;
  const lo = Math.max(0, Math.min(pixelX1, pixelX2));
  const hi = Math.min(renderedWidth, Math.max(pixelX1, pixelX2));
  const startSec = (lo / renderedWidth) * duration;
  // Snapping to the "-1 / to end" sentinel when the drag reaches near the
  // right edge matches natural drag intent -- dragging to the visible end
  // of the track should mean "to the end", not an oddly-precise
  // duration-minus-epsilon value that happens to depend on canvas width.
  const endSec = hi > renderedWidth * 0.99 ? -1 : (hi / renderedWidth) * duration;
  document.getElementById("music-repaint-start").value = startSec.toFixed(2);
  document.getElementById("music-repaint-end").value = endSec === -1 ? -1 : endSec.toFixed(2);
  drawRepaintWaveform();
}

const repaintWaveformCanvas = document.getElementById("music-repaint-waveform");
repaintWaveformCanvas.addEventListener("mousedown", (e) => {
  if (!repaintAudioBuffer) return;
  repaintDragStartX = e.offsetX;
});
repaintWaveformCanvas.addEventListener("mousemove", (e) => {
  if (repaintDragStartX === null) return;
  updateRepaintSelectionFromPixels(repaintDragStartX, e.offsetX);
});
window.addEventListener("mouseup", () => { repaintDragStartX = null; });

// Manual edits to the number fields also keep the highlighted region in
// sync -- dragging and typing are both first-class, neither is a dead end.
document.getElementById("music-repaint-start").addEventListener("input", drawRepaintWaveform);
document.getElementById("music-repaint-end").addEventListener("input", drawRepaintWaveform);

document.getElementById("music-repaint-preview").addEventListener("click", () => {
  if (!repaintAudioBuffer) return;
  const startSec = parseFloat(document.getElementById("music-repaint-start").value) || 0;
  const endRaw = parseFloat(document.getElementById("music-repaint-end").value);
  const endSec = (endRaw < 0 || isNaN(endRaw)) ? repaintAudioBuffer.duration : endRaw;
  const source = repaintAudioCtx.createBufferSource();
  source.buffer = repaintAudioBuffer;
  source.connect(repaintAudioCtx.destination);
  source.start(0, startSec, Math.max(0.01, endSec - startSec));
});

async function loadMusicReferenceLibraryAssets() {
  const assets = await (await fetch("/library")).json();
  const container = document.getElementById("music-reference-picker");
  container.innerHTML = "";
  const audioAssets = assets.filter(
    a => AUDIO_EXTS.includes((a.original_filename.split(".").pop() || "").toLowerCase())
  );
  if (audioAssets.length === 0) {
    container.textContent = "(no songs in the library yet)";
    return;
  }
  for (const asset of audioAssets) {
    const selected = musicReferenceLibraryAssetId === asset.id;
    container.appendChild(assetPickerRow(asset, {
      selected,
      onToggle: () => {
        musicReferenceLibraryAssetId = selected ? null : asset.id;
        renderMusicReferenceLibrarySelection(asset);
        loadMusicReferenceLibraryAssets();
        updateMusicFormForTaskType();
      },
    }));
  }
}

function renderMusicReferenceLibrarySelection(asset) {
  const container = document.getElementById("music-reference-selected");
  container.innerHTML = "";
  if (!musicReferenceLibraryAssetId) return;
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.appendChild(document.createTextNode(assetPickerLabel(asset)));
  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.textContent = "×";
  removeBtn.addEventListener("click", () => {
    musicReferenceLibraryAssetId = null;
    loadMusicReferenceLibraryAssets();
    container.innerHTML = "";
    updateMusicFormForTaskType();
  });
  chip.appendChild(removeBtn);
  container.appendChild(chip);
}

/** Toggles which remix-specific rows are visible for the selected task
 * type, and re-filters the model dropdown to music_remix_models whenever a
 * remix is actually in play (task_type != text2music, OR a reference track
 * is picked regardless of task_type -- style transfer is independent of
 * task_type, see /generate/music's own docstring) -- a non-remix-capable
 * model (Lyria 3) can't be selected through the UI for either case, backed
 * up by the same check server-side. */
let musicGenerationModels = [];
let musicRemixModels = [];

function populateMusicModelOptions(models) {
  const row = document.getElementById("music-model-row");
  const select = document.getElementById("music-model");
  if (models.length > 1) {
    select.innerHTML = "";
    for (const name of models) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
    row.style.display = "block";
  } else {
    row.style.display = "none";
  }
}

function updateMusicFormForTaskType() {
  const taskType = document.getElementById("music-task-type").value;
  document.getElementById("music-source-row").style.display = taskType === "text2music" ? "none" : "block";
  document.getElementById("music-cover-strength-row").style.display = taskType === "cover" ? "block" : "none";
  document.getElementById("music-repaint-row").style.display = taskType === "repaint" ? "block" : "none";
  const isRemix = taskType !== "text2music" || !!musicReferenceLibraryAssetId;
  populateMusicModelOptions(isRemix ? musicRemixModels : musicGenerationModels);
  if (taskType === "repaint" && musicSourceLibraryAssetId) {
    loadRepaintWaveform(musicSourceLibraryAssetId);
  }
}

document.getElementById("music-task-type").addEventListener("change", updateMusicFormForTaskType);

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

/** Shared row renderer for every asset picker (Photo, Music source/
 * reference, Assemble clip/music/sfx) -- replaces what used to be a plain
 * text <button> with a real inline preview (assetPreviewElement itself,
 * sized down via .picker-row's own CSS) next to the label, so a candidate
 * can actually be seen or heard before picking it, not just read as a
 * (often-truncated) prompt/filename string. `selected`/`disabled` drive the
 * trailing button's default label/state; `buttonLabel` overrides that
 * default for callers with a third state (Assemble's "Added, remove via the
 * chip instead" case). `onToggle` is called with no arguments on click --
 * every caller already closes over whatever state it needs to update. */
function assetPickerRow(asset, { selected = false, disabled = false, buttonLabel, onToggle } = {}) {
  const row = document.createElement("div");
  row.className = "picker-row" + (selected ? " selected" : "");

  const preview = assetPreviewElement(asset);
  if (preview.tagName === "VIDEO") {
    // A picker row is for identifying a candidate, not full playback --
    // native controls at this thumbnail size are too cramped to be useful.
    // Silent autoplay-loop instead: for a *cinemagraph* tool specifically,
    // the motion itself is the useful signal, more so than a static frame.
    preview.removeAttribute("controls");
    preview.muted = true;
    preview.loop = true;
    preview.autoplay = true;
    preview.playsInline = true;
  }
  row.appendChild(preview);

  const label = document.createElement("span");
  label.className = "picker-row-label";
  label.textContent = assetPickerLabel(asset);
  row.appendChild(label);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = buttonLabel || (selected ? "Remove" : "Add");
  btn.disabled = disabled;
  btn.addEventListener("click", onToggle);
  row.appendChild(btn);

  return row;
}

/** original_filename/tags are user-supplied (an upload's own name, or
 * free-text tags from a manual `library add`), so every dynamic value here
 * goes through textContent/createElement, never innerHTML -- see this
 * module's docstring. `projects` (already-fetched by loadLibrary, shared
 * across every card in one render pass rather than one GET /projects per
 * card) populates this card's own project <select>. */
function assetCard(asset, projects) {
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

  // Full prompt text, not the truncated version assetPickerLabel uses for
  // compact picker rows -- this is the one place it's worth reading in
  // full. mask_prompt covers the one job type (semantic-mask renders) whose
  // "prompt" describes what to animate rather than what to generate.
  const prompt = asset.provenance && (asset.provenance.prompt || asset.provenance.mask_prompt);
  if (prompt) {
    const promptLine = document.createElement("div");
    promptLine.className = "hint";
    promptLine.textContent = "Prompt: " + prompt;
    meta.appendChild(promptLine);
  }
  card.appendChild(meta);

  const projectRow = document.createElement("div");
  projectRow.className = "asset-project-row";
  const projectSelect = document.createElement("select");
  populateProjectSelect(projectSelect, projects, asset.project || "");
  const projectInput = document.createElement("input");
  projectInput.type = "text";
  projectInput.placeholder = "new project name";
  wireNewProjectInput(projectSelect, projectInput);
  projectSelect.addEventListener("change", async () => {
    if (projectSelect.value === NEW_PROJECT_VALUE) return;  // wait for a real choice
    await fetch(`/library/${asset.id}/project`, {
      method: "POST", body: new URLSearchParams({ project: projectSelect.value }),
    });
    loadLibrary();
  });
  projectInput.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const project = selectedProject(projectSelect, projectInput);
    if (!project) return;
    await fetch(`/library/${asset.id}/project`, { method: "POST", body: new URLSearchParams({ project }) });
    loadLibrary();
  });
  projectRow.appendChild(projectSelect);
  projectRow.appendChild(projectInput);
  card.appendChild(projectRow);

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

/** Refreshes one or more <select>s that list project names as plain filters
 * (Library's own filter + manage dropdowns, Assemble's filter) -- "all"/
 * "(pick a project)"-style placeholders, not populateProjectSelect's "No
 * project"/"+ New project..." shape, since these pick among *existing*
 * projects rather than assigning one. `selectSpecs` is a list of
 * [elementId, placeholderText] pairs. */
function refreshProjectFilterSelects(projects, selectSpecs) {
  for (const [id, placeholder] of selectSpecs) {
    const select = document.getElementById(id);
    const current = select.value;
    select.innerHTML = "";
    const placeholderOpt = document.createElement("option");
    placeholderOpt.value = "";
    placeholderOpt.textContent = placeholder;
    select.appendChild(placeholderOpt);
    for (const name of projects) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
    select.value = projects.includes(current) ? current : "";
  }
}

async function loadLibrary() {
  const listEl = document.getElementById("library-list");
  const errorEl = document.getElementById("library-error");
  errorEl.textContent = "";
  listEl.textContent = "Loading...";

  const kind = document.getElementById("library-kind-filter").value;
  const tag = document.getElementById("library-tag-filter").value.trim();
  const project = document.getElementById("library-project-filter").value;
  const params = new URLSearchParams();
  if (kind) params.set("kind", kind);
  if (tag) params.set("tag", tag);
  if (project) params.set("project", project);

  try {
    const [res, projects] = await Promise.all([fetch(`/library?${params}`), fetchProjects()]);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const assets = await res.json();
    refreshProjectFilterSelects(projects, [
      ["library-project-filter", "all"],
      ["library-project-manage", "(pick a project)"],
    ]);

    listEl.innerHTML = "";
    if (assets.length === 0) {
      listEl.textContent = "No assets yet.";
      return;
    }
    for (const asset of assets) listEl.appendChild(assetCard(asset, projects));
  } catch (err) {
    listEl.textContent = "";
    errorEl.textContent = err.message;
  }
}

document.getElementById("library-project-filter").addEventListener("change", loadLibrary);

document.getElementById("library-project-rename").addEventListener("click", async () => {
  const errorEl = document.getElementById("library-error");
  const old = document.getElementById("library-project-manage").value;
  const to = document.getElementById("library-project-rename-to").value.trim();
  if (!old || !to) { errorEl.textContent = "Pick a project and type a new name."; return; }
  errorEl.textContent = "";
  await fetch("/projects/rename", { method: "POST", body: new URLSearchParams({ old, new: to }) });
  document.getElementById("library-project-rename-to").value = "";
  // If the active filter was pointed at the name that just got renamed,
  // repoint it too -- otherwise loadLibrary() below would fetch with the
  // now-nonexistent old name and show "No assets yet" until the next
  // manual refresh, even though the renamed project's assets are right there.
  const filterSelect = document.getElementById("library-project-filter");
  if (filterSelect.value === old) filterSelect.value = to;
  loadLibrary();
});

document.getElementById("library-project-delete").addEventListener("click", async () => {
  const errorEl = document.getElementById("library-error");
  const name = document.getElementById("library-project-manage").value;
  if (!name) { errorEl.textContent = "Pick a project to delete."; return; }
  if (!confirm(`Remove project "${name}" from all its assets? The assets themselves are kept.`)) return;
  errorEl.textContent = "";
  await fetch(`/projects/${encodeURIComponent(name)}`, { method: "DELETE" });
  loadLibrary();
});

/** Shared polling loop -- every /generate/* and /render/* route returns the
 * same {job_id} shape and is checked via the same GET /jobs/{id} contract,
 * so one implementation covers all six tabs. `onDone` wires the result
 * into whichever <video>/<audio> element belongs to that tab, and now also
 * receives the full job object (job.can_save) so callers can decide
 * whether to offer saving. */
async function pollJob(jobId, { statusEl, errorEl, onDone }) {
  for (;;) {
    const res = await fetch(`/jobs/${jobId}`);
    const job = await res.json();
    statusEl.textContent = `Job ${jobId}: ${job.status}`;
    if (job.status === "done") {
      onDone(`/jobs/${jobId}/file`, job);
      return;
    }
    if (job.status === "error") {
      errorEl.textContent = job.error;
      return;
    }
    await new Promise(r => setTimeout(r, 1500));
  }
}

/** Nothing is auto-saved to the library anymore -- generation/render jobs
 * stage a save candidate at completion (server/jobs.py's pending_library)
 * but only POST /jobs/{id}/save actually registers it, once the caller has
 * seen/heard the result and decided to keep it. One shared button/handler
 * (not six copies) since the behavior is identical everywhere it's used:
 * inserted right after the preview element, disabled + relabeled while the
 * request is in flight, "✓ Saved" on success, an error via the same
 * errorEl every form already has on failure.
 *
 * The project picker next to it (2026-08-05) is this feature's own
 * "decide at save time" moment for project assignment too -- see
 * service.save_job_to_library's docstring. Defaulting to "No project"
 * keeps the common case (just save it) a single click, same as before. */
async function showSaveButton(jobId, afterEl, errorEl) {
  const existing = afterEl.nextElementSibling;
  if (existing && existing.classList.contains("project-picker")) existing.remove();

  const wrap = document.createElement("div");
  wrap.className = "project-picker";

  const projectSelect = document.createElement("select");
  const projectInput = document.createElement("input");
  projectInput.type = "text";
  projectInput.placeholder = "new project name";
  wireNewProjectInput(projectSelect, projectInput);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "save-to-library-btn";
  btn.textContent = "Save to library";
  btn.addEventListener("click", async () => {
    errorEl.textContent = "";
    btn.disabled = true;
    btn.textContent = "Saving...";
    try {
      const project = selectedProject(projectSelect, projectInput);
      const res = await fetch(`/jobs/${jobId}/save`, {
        method: "POST", body: new URLSearchParams(project ? { project } : {}),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      btn.textContent = "✓ Saved";
      projectSelect.disabled = true;
      projectInput.disabled = true;
    } catch (err) {
      errorEl.textContent = err.message;
      btn.textContent = "Save to library";
      btn.disabled = false;
    }
  });

  wrap.appendChild(projectSelect);
  wrap.appendChild(projectInput);
  wrap.appendChild(btn);
  afterEl.insertAdjacentElement("afterend", wrap);

  populateProjectSelect(projectSelect, await fetchProjects(), "");
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
        onDone: (url, job) => {
          previewEl.src = url;
          previewEl.style.display = "block";
          if (job.can_save) showSaveButton(job.job_id, previewEl, errorEl);
        },
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
    const taskType = document.getElementById("music-task-type").value;
    if (taskType !== "text2music" && !musicSourceLibraryAssetId) {
      document.getElementById("music-error").textContent = `task_type="${taskType}" needs a song picked to remix.`;
      return null;
    }
    const form = new FormData();
    form.append("prompt", prompt);
    form.append("lyrics", document.getElementById("music-lyrics").value);
    form.append("duration", document.getElementById("music-duration").value);
    form.append("thinking", document.getElementById("music-thinking").checked);
    form.append("instrumental", document.getElementById("music-instrumental").checked);
    form.append("task_type", taskType);
    if (musicSourceLibraryAssetId) form.append("src_audio_asset_id", musicSourceLibraryAssetId);
    if (musicReferenceLibraryAssetId) form.append("reference_audio_asset_id", musicReferenceLibraryAssetId);
    if (taskType === "cover") {
      form.append("cover_strength", document.getElementById("music-cover-strength").value);
    }
    if (taskType === "repaint") {
      form.append("repainting_start", document.getElementById("music-repaint-start").value);
      form.append("repainting_end", document.getElementById("music-repaint-end").value);
    }
    const modelSelect = document.getElementById("music-model");
    if (modelSelect.value) {
      form.append("model", modelSelect.value);
    }
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
  const projectSelect = document.getElementById("assemble-project-filter");
  const params = new URLSearchParams({ kind: "generated" });
  if (projectSelect.value) params.set("project", projectSelect.value);

  const [assets, projects] = await Promise.all([
    (async () => (await fetch(`/library?${params}`)).json())(),
    fetchProjects(),
  ]);
  refreshProjectFilterSelects(projects, [["assemble-project-filter", "all"]]);

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
    const selected = assembleState[bucket].includes(asset.id);
    // allowMultiple (sound effects): every click toggles this one asset in
    // or out. Ordered buckets (clips/music): once selected, the row
    // disables -- removing happens via the chip's own × below, since
    // clicking here again wouldn't disambiguate *which* occurrence to drop
    // if the same asset were ever added twice.
    const disabled = selected && !allowMultiple;
    container.appendChild(assetPickerRow(asset, {
      selected,
      disabled,
      buttonLabel: disabled ? "Added" : (selected ? "Remove" : "Add"),
      onToggle: () => {
        if (allowMultiple && selected) {
          assembleState[bucket].splice(assembleState[bucket].indexOf(asset.id), 1);
        } else {
          assembleState[bucket].push(asset.id);
        }
        renderAssemblePicker(containerId, assets, bucket, allowMultiple);
        renderAssembleChips();
      },
    }));
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
document.getElementById("assemble-project-filter").addEventListener("change", loadAssembleAssets);
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
      onDone: (url, job) => {
        previewEl.src = url;
        previewEl.style.display = "block";
        if (job.can_save) showSaveButton(job.job_id, previewEl, errorEl);
      },
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
loadMusicSourceLibraryAssets();
loadMusicReferenceLibraryAssets();
</script>
</body>
</html>
"""

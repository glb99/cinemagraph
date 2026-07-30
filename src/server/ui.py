"""A minimal, self-contained web UI: one HTML page, inline CSS/JS, no build
step, no separate static-file packaging concerns (see the api-to-server move
in docs/DESIGN.md's decision log for why non-.py assets are a real footgun
here -- this sidesteps that entirely by being plain Python source).

Six tabs: photo rendering, video rendering, music generation, sound-effect
generation, image generation, and a library browser. Photo/video/library are
always available; music, sound effects, and image generation are feature-gated
on GET /capabilities, same signal the rest of the system already uses to
degrade gracefully when an optional service isn't running -- their tab buttons
are hidden entirely rather than shown-disabled, matching how mask_prompt was
already hidden in the original photo-only version of this page. Per-effect
override flags (--rain-count etc. on the CLI) and video's grade fine-tuning
knobs are intentionally left out of every form here, same as the original
photo tab -- this is a thin client covering the common path, not full parity
with every CLI flag.

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
  .tab { display: none; }
  .tab.active { display: block; }
  .library-toolbar { display: flex; gap: 0.6rem; align-items: center; margin-bottom: 1rem; }
  .library-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 1rem; }
  .library-card { border: 1px solid #333; border-radius: 8px; padding: 0.6rem; }
  .library-card img, .library-card video, .library-card audio { max-width: 100%; border-radius: 6px; margin-top: 0; }
  .library-meta { margin: 0.5rem 0; font-size: 0.85rem; }
  .library-card button { padding: 0.3rem 0.7rem; font-size: 0.85rem; background: #555; }
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
    <label>Photo <input type="file" id="photo-input" accept="image/*" required></label>
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
  </fieldset>
  <button type="submit">Render</button>
</form>
<div class="status" id="video-status"></div>
<div class="error" id="video-error"></div>
<video id="video-preview" controls loop style="display:none"></video>
</section>

<!-- Music -->
<section class="tab" id="tab-music">
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
<form id="image-form">
  <div class="row">
    <label style="display:block">Prompt
      <input type="text" id="image-prompt" placeholder='e.g. "a lofi bedroom at sunset, warm light"'>
    </label>
  </div>
  <div class="row"></div>
  <button type="submit">Generate</button>
</form>
<div class="status" id="image-status"></div>
<div class="error" id="image-error"></div>
<img id="image-preview" class="preview" style="display:none">
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
  { id: "music", label: "Music", capability: "music_generation" },
  { id: "sfx", label: "Sound effects", capability: "sound_effect_generation" },
  { id: "image", label: "Image", capability: "image_generation" },
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
}

async function loadCapabilities() {
  const caps = await (await fetch("/capabilities")).json();
  if (caps.semantic_mask) {
    document.getElementById("photo-mask-prompt-row").style.display = "block";
  }

  const nav = document.getElementById("nav");
  let firstVisible = null;
  for (const tab of TABS) {
    if (!tab.always && !caps[tab.capability]) continue;
    if (firstVisible === null) firstVisible = tab.id;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = tab.label;
    btn.dataset.tab = tab.id;
    btn.addEventListener("click", () => showTab(tab.id));
    nav.appendChild(btn);
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
    const maskFile = document.getElementById("photo-mask").files[0];
    const maskPrompt = document.getElementById("photo-mask-prompt").value.trim();
    if (maskFile && maskPrompt) {
      document.getElementById("photo-error").textContent = "Supply either a mask file or a mask prompt, not both.";
      return null;
    }
    const form = new FormData();
    form.append("input_file", document.getElementById("photo-input").files[0]);
    for (const eff of effects) form.append("effect", eff);
    if (maskFile) form.append("mask", maskFile);
    if (maskPrompt) form.append("mask_prompt", maskPrompt);
    form.append("duration", document.getElementById("photo-duration").value);
    form.append("fps", document.getElementById("photo-fps").value);
    form.append("speed", document.getElementById("photo-speed").value);
    return form;
  },
});

wireForm("video-form", {
  endpoint: "/render/video",
  statusId: "video-status", errorId: "video-error", previewId: "video-preview",
  buildForm: () => {
    const form = new FormData();
    form.append("input_file", document.getElementById("video-input").files[0]);
    const maskFile = document.getElementById("video-mask").files[0];
    if (maskFile) form.append("mask", maskFile);
    form.append("mask_threshold", document.getElementById("video-mask-threshold").value);
    form.append("still_frame_index", document.getElementById("video-still-frame").value);
    form.append("blend_frames", document.getElementById("video-blend-frames").value);
    form.append("auto_trim", document.getElementById("video-auto-trim").checked);
    form.append("also_gif", document.getElementById("video-gif").checked);
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
    return form;
  },
});

document.getElementById("library-refresh").addEventListener("click", loadLibrary);
document.getElementById("library-kind-filter").addEventListener("change", loadLibrary);
document.getElementById("library-tag-filter").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); loadLibrary(); }
});

loadCapabilities();
loadEffects();
</script>
</body>
</html>
"""

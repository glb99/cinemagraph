"""A minimal, self-contained web UI: one HTML page, inline CSS/JS, no build
step, no separate static-file packaging concerns (see the api-to-server move
in docs/DESIGN.md's decision log for why non-.py assets are a real footgun
here -- this sidesteps that entirely by being plain Python source).

Scope deliberately small for a first version: photo-to-cinemagraph rendering
only (upload, effect selection, optional mask upload or mask_prompt, submit,
poll, preview). Video rendering / music / sound-effect generation are follow-
ups, not started here -- see docs/DESIGN.md sec 5.5.

Feature-gating: the page fetches GET /capabilities on load and only shows the
mask_prompt (semantic masking) input when semantic_mask is true, exactly the
same signal the rest of the system already uses to degrade gracefully when
an optional service isn't running.
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
  input[type="text"] { width: 100%; box-sizing: border-box; }
  button {
    background: #3a6ff0; color: white; border: none; border-radius: 6px;
    padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer;
  }
  button:disabled { background: #555; cursor: not-allowed; }
  #status { margin-top: 1rem; font-size: 0.9rem; color: #aaa; }
  #error { color: #ff6b6b; white-space: pre-wrap; }
  video { max-width: 100%; margin-top: 1rem; border-radius: 8px; }
  .row { margin-bottom: 0.6rem; }
  .hint { color: #888; font-size: 0.8rem; }
</style>
</head>
<body>
<h1>cinemagraph-tool</h1>
<p class="hint">Animate a photo into a looping cinemagraph. A thin client over the same API a
script or curl would use -- nothing here that <code>POST /render/photo</code> doesn't do itself.</p>

<form id="form">
  <div class="row">
    <label>Photo <input type="file" id="photo" accept="image/*" required></label>
  </div>

  <fieldset>
    <legend>Effects</legend>
    <div id="effects">(loading...)</div>
  </fieldset>

  <fieldset>
    <legend>Mask (optional -- omit to animate the whole photo)</legend>
    <div class="row">
      <label>Hand-painted mask file <input type="file" id="mask" accept="image/png"></label>
    </div>
    <div class="row" id="mask-prompt-row" style="display:none">
      <label style="display:block">Or describe what to animate (semantic masking)
        <input type="text" id="mask_prompt" placeholder='e.g. "clouds", "sun", "water"'>
      </label>
    </div>
  </fieldset>

  <fieldset>
    <legend>Timing</legend>
    <label>Duration (s) <input type="number" id="duration" value="4" min="0.5" step="0.5"></label>
    <label>FPS <input type="number" id="fps" value="30" min="1"></label>
    <label>Speed <input type="number" id="speed" value="1.0" min="0.1" step="0.1"></label>
  </fieldset>

  <button type="submit" id="submit">Render</button>
</form>

<div id="status"></div>
<div id="error"></div>
<video id="preview" controls loop style="display:none"></video>

<script>
async function loadCapabilities() {
  const caps = await (await fetch("/capabilities")).json();
  if (caps.semantic_mask) {
    document.getElementById("mask-prompt-row").style.display = "block";
  }
}

async function loadEffects() {
  const data = await (await fetch("/effects")).json();
  const container = document.getElementById("effects");
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

async function pollJob(jobId) {
  const statusEl = document.getElementById("status");
  for (;;) {
    const res = await fetch(`/jobs/${jobId}`);
    const job = await res.json();
    statusEl.textContent = `Job ${jobId}: ${job.status}`;
    if (job.status === "done") {
      const video = document.getElementById("preview");
      video.src = `/jobs/${jobId}/file`;
      video.style.display = "block";
      return;
    }
    if (job.status === "error") {
      document.getElementById("error").textContent = job.error;
      return;
    }
    await new Promise(r => setTimeout(r, 1500));
  }
}

document.getElementById("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  document.getElementById("error").textContent = "";
  document.getElementById("preview").style.display = "none";
  const submitBtn = document.getElementById("submit");
  submitBtn.disabled = true;

  const effects = selectedEffects();
  if (effects.length === 0) {
    document.getElementById("error").textContent = "Select at least one effect.";
    submitBtn.disabled = false;
    return;
  }

  const maskFile = document.getElementById("mask").files[0];
  const maskPrompt = document.getElementById("mask_prompt").value.trim();
  if (maskFile && maskPrompt) {
    document.getElementById("error").textContent = "Supply either a mask file or a mask prompt, not both.";
    submitBtn.disabled = false;
    return;
  }

  const form = new FormData();
  form.append("input_file", document.getElementById("photo").files[0]);
  for (const eff of effects) form.append("effect", eff);
  if (maskFile) form.append("mask", maskFile);
  if (maskPrompt) form.append("mask_prompt", maskPrompt);
  form.append("duration", document.getElementById("duration").value);
  form.append("fps", document.getElementById("fps").value);
  form.append("speed", document.getElementById("speed").value);

  try {
    const res = await fetch("/render/photo", { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    const { job_id } = await res.json();
    document.getElementById("status").textContent = `Job ${job_id}: submitted`;
    await pollJob(job_id);
  } catch (err) {
    document.getElementById("error").textContent = err.message;
  } finally {
    submitBtn.disabled = false;
  }
});

loadCapabilities();
loadEffects();
</script>
</body>
</html>
"""

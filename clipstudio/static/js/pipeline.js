import { clearClips, clearRuns, createRun, deleteClip, deleteRun, getPipelines, listClips, listProviders, listRuns, rerunRun } from './api.js';
import { aiChoiceFromPrefs } from './ai-prefs.js';
import { escapeHtml, renderNav, showMessage } from './ui.js';
import { clipCard, clipName, onSend, setSendStatus, showActiveRunCount } from './widgets.js';

/**
 * @typedef {import('./api.js').Run} Run
 * @typedef {import('./api.js').StepRecord} StepRecord
 */

const POLL_MS = 1000;
const RUN_LABELS = { queued: 'Queued', running: 'Running', done: 'Done', failed: 'Failed' };
const STEP_LABELS = { queued: 'Queued', running: 'Running', done: 'Done', failed: 'Failed', skipped: 'Skipped' };

const runsList = document.getElementById('runsList');
const clipsList = document.getElementById('clipsList');
const aiChoiceNote = document.getElementById('aiChoice');
const pageError = document.getElementById('pageError');
const clearRunsBtn = document.getElementById('clearRunsBtn');
const clearClipsBtn = document.getElementById('clearClipsBtn');

let pipelines = [];
const clips = new Map();
// Cards are kept per id and re-rendered only when their markup changes, so a playing preview keeps playing.
const runCards = new Map();
const clipCards = new Map();
let pollTimer = null;
let pollRound = 0;

function showError(text) {
  pageError.textContent = text || '';
  pageError.hidden = !text;
}

function elapsed(step) {
  if (!step.started_at) return '';
  const seconds = Math.max(0, (step.finished_at ?? Date.now() / 1000) - step.started_at);
  return seconds < 60 ? `${seconds.toFixed(1)} s` : `${Math.floor(seconds / 60)} min ${Math.floor(seconds % 60)} s`;
}

/** @param {StepRecord} step */
function stepHtml(step) {
  const spinner = step.state === 'running' ? '<span class="spinner" aria-hidden="true"></span>' : '';
  return `
    <li class="run-step" data-state="${step.state}">
      <span class="step-state">${spinner}${STEP_LABELS[step.state]}</span>
      <span class="step-label">${escapeHtml(step.label)}</span>
      <span class="step-time">${elapsed(step)}</span>
      ${step.detail ? `<p class="step-detail">${escapeHtml(step.detail)}</p>` : ''}
    </li>`;
}

/** @param {Run} run */
function runHtml(run) {
  const input = clips.get(run.input_clip_id);
  const output = run.output_clip_id && clips.get(run.output_clip_id);
  const label = pipelines.find((p) => p.id === run.pipeline)?.label || run.pipeline;
  const canDelete = run.status === 'done' || run.status === 'failed';
  return `
    <header class="run-head">
      <h2 class="run-title">${escapeHtml(label)}</h2>
      <div class="run-head-actions">
        <span class="run-status" data-status="${run.status}">${RUN_LABELS[run.status]}</span>
        ${canDelete ? `<button type="button" class="btn btn-sm btn-icon" data-action="delete-run"
          data-run-id="${escapeHtml(run.run_id)}" title="Delete run" aria-label="Delete run">✕</button>` : ''}
      </div>
    </header>
    <p class="run-input">From ${input ? escapeHtml(clipName(input)) : 'a clip'}</p>
    <ol class="run-steps">${run.steps.map(stepHtml).join('')}</ol>
    ${output ? clipCard(output, pipelines) : ''}
    ${canDelete ? `
      <div class="run-actions">
        <button type="button" class="btn btn-sm" data-action="rerun" data-run-id="${escapeHtml(run.run_id)}"
          data-replaces="${run.output_clip_id ? 'true' : ''}">${run.status === 'failed' ? 'Retry' : 'Run again'}</button>
        <p class="send-status" role="status"></p>
      </div>` : ''}`;
}

function renderKeyed(container, cards, items, keyOf, makeElement, htmlOf, emptyText) {
  if (items.length === 0) {
    for (const card of cards.values()) card.el.remove();
    cards.clear();
    showMessage(container, emptyText);
    return;
  }
  const currentKeys = new Set(items.map(keyOf));
  for (const [key, card] of cards.entries()) {
    if (!currentKeys.has(key)) {
      card.el.remove();
      cards.delete(key);
    }
  }
  container.querySelector(':scope > .empty')?.remove();
  items.forEach((item, index) => {
    const key = keyOf(item);
    let card = cards.get(key);
    if (!card) {
      card = { el: makeElement(item), html: '' };
      cards.set(key, card);
    }
    const html = htmlOf(item);
    if (card.html !== html) {
      card.el.innerHTML = html;
      card.html = html;
    }
    if (container.children[index] !== card.el) container.insertBefore(card.el, container.children[index] ?? null);
  });
}

function renderRuns(runs) {
  if (clearRunsBtn) {
    clearRunsBtn.hidden = !runs.some((r) => r.status === 'done' || r.status === 'failed');
  }
  renderKeyed(runsList, runCards, runs, (run) => run.run_id, (run) => {
    const el = document.createElement('article');
    el.className = 'run-card';
    el.id = `run-${run.run_id}`;
    return el;
  }, runHtml, 'No runs yet. Export a clip in the Editor with "Then send to" set, or send one from the clips list.');
}

function renderClips(list) {
  clearClipsBtn.hidden = list.length === 0;
  renderKeyed(clipsList, clipCards, list, (clip) => clip.clip_id, () => document.createElement('div'),
    (clip) => clipCard(clip, pipelines), 'No clips yet. Export one in the Editor.');
}

async function refreshClips() {
  const list = await listClips();
  for (const clip of list) clips.set(clip.clip_id, clip);
  renderClips(list);
}

async function refresh() {
  clearTimeout(pollTimer);
  // A Send can start a refresh while a timed one is in flight. Only the newest may schedule the next poll.
  const round = ++pollRound;
  try {
    const runs = await listRuns();
    if (runs.some((r) => !clips.has(r.input_clip_id) || (r.output_clip_id && !clips.has(r.output_clip_id)))) {
      await refreshClips();
    }
    renderRuns(runs);
    showError(null);
    if (showActiveRunCount(runs) && round === pollRound) pollTimer = setTimeout(refresh, POLL_MS);
  } catch (err) {
    showError(`${err.message}. Trying again…`);
    if (round === pollRound) pollTimer = setTimeout(refresh, 3 * POLL_MS);
  }
}

function focusRun(runId) {
  const card = runCards.get(runId);
  for (const other of runCards.values()) other.el.classList.toggle('is-target', other === card);
  if (!card) {
    showError('That run is not in the list.');
    return;
  }
  const smooth = !matchMedia('(prefers-reduced-motion: reduce)').matches;
  card.el.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' });
}

async function startRun(clipId, pipeline, button, status) {
  button.disabled = true;
  setSendStatus(status, 'Sending…');
  try {
    const run = await createRun({ clip_id: clipId, pipeline, ai: aiChoiceFromPrefs() });
    setSendStatus(status, '');
    await refresh();
    focusRun(run.run_id);
  } catch (err) {
    setSendStatus(status, err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function retryRun(runId, button, status) {
  button.disabled = true;
  setSendStatus(status, 'Retrying…');
  try {
    await rerunRun(runId, { ai: aiChoiceFromPrefs() });
    setSendStatus(status, '');
    await refreshClips();
    await refresh();
    focusRun(runId);
  } catch (err) {
    setSendStatus(status, err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function showAiChoice() {
  const choice = aiChoiceFromPrefs();
  let label = choice.provider;
  try {
    const { providers, auto } = await listProviders();
    const labels = new Map(providers.map((p) => [p.id, p.label]));
    label = choice.provider === 'auto'
      ? (auto ? `Automatic (${labels.get(auto) || auto})` : 'Automatic')
      : labels.get(choice.provider) || choice.provider;
  } catch {}
  const model = choice.model ? `, ${escapeHtml(choice.model)}` : '';
  aiChoiceNote.innerHTML = `AI: ${escapeHtml(label)}${model} (change it in the <a href="/">Editor</a>)`;
}

onSend(document.body, ({ clipId, pipeline, button, status }) => startRun(clipId, pipeline, button, status));

runsList.addEventListener('click', (e) => {
  const button = e.target.closest('[data-action="rerun"]');
  if (!button) return;
  if (button.dataset.replaces && !confirm('Run this again on the same clip? Its current result is replaced.')) return;
  retryRun(button.dataset.runId, button, button.nextElementSibling);
});

document.body.addEventListener('click', async (e) => {
  const delClipBtn = e.target.closest('[data-action="delete-clip"]');
  if (delClipBtn) {
    const clipId = delClipBtn.dataset.clipId;
    if (!confirm('Permanently delete this clip? This removes the video file from disk and cannot be undone.')) return;
    delClipBtn.disabled = true;
    try {
      await deleteClip(clipId);
      clips.delete(clipId);
      await refreshClips();
      await refresh();
    } catch (err) {
      showError(err.message);
      delClipBtn.disabled = false;
    }
    return;
  }

  const delRunBtn = e.target.closest('[data-action="delete-run"]');
  if (delRunBtn) {
    const runId = delRunBtn.dataset.runId;
    delRunBtn.disabled = true;
    try {
      await deleteRun(runId);
      await refresh();
    } catch (err) {
      showError(err.message);
      delRunBtn.disabled = false;
    }
    return;
  }
});

if (clearRunsBtn) {
  clearRunsBtn.addEventListener('click', async () => {
    if (!confirm('Clear all finished runs?')) return;
    clearRunsBtn.disabled = true;
    try {
      await clearRuns();
      await refresh();
    } catch (err) {
      showError(err.message);
    } finally {
      clearRunsBtn.disabled = false;
    }
  });
}

clearClipsBtn.addEventListener('click', async () => {
  if (!confirm('Permanently delete every clip? This removes the video files from disk and cannot be undone.')) return;
  clearClipsBtn.disabled = true;
  try {
    const { kept } = await clearClips();
    clips.clear();
    await refreshClips();
    await refresh();
    showError(kept ? `Kept ${kept} ${kept === 1 ? 'clip' : 'clips'} that a run is still using.` : null);
  } catch (err) {
    showError(err.message);
  } finally {
    clearClipsBtn.disabled = false;
  }
});

// Another tab may have exported or sent a clip while this one was not polling.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshClips().then(refresh).catch((err) => showError(err.message));
});

async function start() {
  renderNav();
  showAiChoice();
  try {
    pipelines = await getPipelines();
    await refreshClips();
  } catch (err) {
    showError(err.message);
  }
  await refresh();
  const target = new URLSearchParams(location.search).get('run');
  if (target) focusRun(target);
}

start();

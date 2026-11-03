import {
  createClip, findHighlights, forgetProviderSettings, getCaptions, getVideoInfo, downloadVideo, listProviders,
  saveProviderSettings,
} from './api.js';
import { readAiPrefs, saveAiPrefs } from './ai-prefs.js';
import { countOf, escapeHtml, formatTime, showMessage } from './ui.js';

let currentVideoId = null;
let videoDuration = 0;
let clipStart = 0;
let clipEnd = 0;
let allCaptions = [];

const videoPlayer = document.getElementById('videoPlayer');
const placeholderView = document.getElementById('placeholderView');
const urlInput = document.getElementById('urlInput');
const loadBtn = document.getElementById('loadBtn');
const fetchCcTopBtn = document.getElementById('fetchCcTopBtn');
const loadStatus = document.getElementById('loadStatus');
const startSlider = document.getElementById('startSlider');
const endSlider = document.getElementById('endSlider');
const rangeFill = document.getElementById('rangeFill');
const playheadMarker = document.getElementById('playheadMarker');
const startTimeVal = document.getElementById('startTimeVal');
const endTimeVal = document.getElementById('endTimeVal');
const clipDurationPill = document.getElementById('clipDurationPill');
const setStartBtn = document.getElementById('setStartBtn');
const setEndBtn = document.getElementById('setEndBtn');
const stepBackBtn = document.getElementById('stepBackBtn');
const stepForwardBtn = document.getElementById('stepForwardBtn');
const previewClipBtn = document.getElementById('previewClipBtn');
const resetRangeBtn = document.getElementById('resetRangeBtn');
const exportBtn = document.getElementById('exportBtn');
const aiAnalyzeBtn = document.getElementById('aiAnalyzeBtn');
const highlightsList = document.getElementById('highlightsList');
const captionsList = document.getElementById('captionsList');
const searchCaptionsInput = document.getElementById('searchCaptionsInput');
const geminiApiKey = document.getElementById('geminiApiKey');
const aiProviderSelect = document.getElementById('aiProviderSelect');
const aiModelInput = document.getElementById('aiModelInput');
const aiBaseUrlInput = document.getElementById('aiBaseUrlInput');
const aiQueryInput = document.getElementById('aiQueryInput');
const aiProviderHint = document.getElementById('aiProviderHint');
const aiRememberRow = document.getElementById('aiRememberRow');
const aiRememberInput = document.getElementById('aiRememberInput');
const aiForgetBtn = document.getElementById('aiForgetBtn');
const aiAnswered = document.getElementById('aiAnswered');
const loadError = document.getElementById('loadError');

const PLATFORM_LABELS = { youtube: 'YouTube', twitch: 'Twitch', kick: 'Kick' };
const NO_SUBTITLES = 'This video has no subtitles, so the AI cannot search it. You can still trim the range by hand.';

let aiProviders = new Map();
let aiAuto = null;
let aiPrefs = readAiPrefs();

function switchTab(tab) {
  for (const panel of document.querySelectorAll('[data-tab-panel]')) panel.hidden = panel.dataset.tabPanel !== tab;
  for (const btn of document.querySelectorAll('[data-tab]')) btn.classList.toggle('active', btn.dataset.tab === tab);
}

for (const btn of document.querySelectorAll('[data-tab]')) {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
}

function setStatus(text, loading = false) {
  if (loading) {
    loadStatus.innerHTML = `<span class="spinner"></span> <span>${text}</span>`;
  } else {
    loadStatus.textContent = text;
  }
}

async function loadVideo() {
  const url = urlInput.value.trim();
  if (!url) {
    showLoadError('Paste a YouTube, Twitch or Kick link first.');
    return;
  }

  showLoadError(null);
  setStatus('Fetching video info…', true);
  loadBtn.disabled = true;

  try {
    const info = await getVideoInfo(url);
    currentVideoId = info.video_id;
    exportBtn.disabled = true;
    showMessage(captionsList, 'Fetching subtitles…');
    showMessage(highlightsList, 'Fetching subtitles…');
    videoDuration = info.duration || 100;
    allCaptions = [];
    aiAnswered.hidden = true;
    updateFindMomentsButton();

    document.getElementById('videoMetaHeader').hidden = false;
    document.getElementById('videoTitleDisplay').textContent = info.title;
    document.getElementById('videoPlatformDisplay').textContent = PLATFORM_LABELS[info.platform] || info.platform || '';
    document.getElementById('videoDurationDisplay').textContent = info.duration ? formatTime(info.duration) : '';

    setStatus('Downloading video…', true);
    const dlData = await downloadVideo(url);

    placeholderView.hidden = true;
    videoPlayer.hidden = false;
    videoPlayer.src = dlData.stream_url;
    videoPlayer.load();

    videoPlayer.onloadedmetadata = () => {
      videoDuration = videoPlayer.duration || videoDuration;
      document.getElementById('videoDurationDisplay').textContent = formatTime(videoDuration);
      initSliders();
      exportBtn.disabled = false;
    };

    fetchCaptions();
  } catch (err) {
    console.error(err);
    setStatus('Link not loaded');
    showLoadError(err.message);
  } finally {
    loadBtn.disabled = false;
  }
}

function initSliders() {
  startSlider.max = videoDuration;
  endSlider.max = videoDuration;
  startSlider.value = 0;
  endSlider.value = Math.min(30, videoDuration);
  clipStart = 0;
  clipEnd = parseFloat(endSlider.value);
  updateTimelineUI();
}

function updateTimelineUI() {
  clipStart = Math.min(parseFloat(startSlider.value), parseFloat(endSlider.value) - 0.5);
  clipEnd = Math.max(parseFloat(endSlider.value), clipStart + 0.5);

  startSlider.value = clipStart;
  endSlider.value = clipEnd;
  renderRange();
}

function renderRange() {
  startTimeVal.textContent = formatTime(clipStart);
  endTimeVal.textContent = formatTime(clipEnd);
  const dur = Math.max(0, clipEnd - clipStart);
  clipDurationPill.textContent = formatTime(dur);

  const startPercent = (clipStart / videoDuration) * 100;
  const endPercent = (clipEnd / videoDuration) * 100;
  rangeFill.style.left = `${startPercent}%`;
  rangeFill.style.width = `${Math.max(0, endPercent - startPercent)}%`;
}

startSlider.addEventListener('input', () => {
  if (parseFloat(startSlider.value) >= parseFloat(endSlider.value)) {
    startSlider.value = parseFloat(endSlider.value) - 0.5;
  }
  updateTimelineUI();
  videoPlayer.currentTime = clipStart;
});

endSlider.addEventListener('input', () => {
  if (parseFloat(endSlider.value) <= parseFloat(startSlider.value)) {
    endSlider.value = parseFloat(startSlider.value) + 0.5;
  }
  updateTimelineUI();
  videoPlayer.currentTime = clipEnd;
});

videoPlayer.addEventListener('timeupdate', () => {
  if (videoDuration > 0) {
    const percent = (videoPlayer.currentTime / videoDuration) * 100;
    playheadMarker.style.left = `${percent}%`;
  }
});

setStartBtn.addEventListener('click', () => {
  const cur = videoPlayer.currentTime;
  if (cur < clipEnd) {
    startSlider.value = cur;
    updateTimelineUI();
  }
});

setEndBtn.addEventListener('click', () => {
  const cur = videoPlayer.currentTime;
  if (cur > clipStart) {
    endSlider.value = cur;
    updateTimelineUI();
  }
});

stepBackBtn.addEventListener('click', () => {
  videoPlayer.currentTime = Math.max(0, videoPlayer.currentTime - 0.1);
});

stepForwardBtn.addEventListener('click', () => {
  videoPlayer.currentTime = Math.min(videoDuration, videoPlayer.currentTime + 0.1);
});

previewClipBtn.addEventListener('click', () => {
  videoPlayer.currentTime = clipStart;
  videoPlayer.play();
  const checkLoop = () => {
    if (videoPlayer.currentTime >= clipEnd) {
      videoPlayer.pause();
      videoPlayer.removeEventListener('timeupdate', checkLoop);
    }
  };
  videoPlayer.addEventListener('timeupdate', checkLoop);
});

resetRangeBtn.addEventListener('click', () => {
  initSliders();
});

async function fetchCaptions() {
  const url = urlInput.value.trim();
  if (!url) return;

  setStatus('Fetching subtitles…', true);
  try {
    const data = await getCaptions(url);
    allCaptions = data.captions || [];
    updateFindMomentsButton();
    if (allCaptions.length === 0) {
      showMessage(captionsList, NO_SUBTITLES);
      showMessage(highlightsList, NO_SUBTITLES);
      setStatus('No subtitles for this video');
      return;
    }
    renderCaptions(allCaptions);
    showMessage(highlightsList, `${countOf(allCaptions.length, 'subtitle line')} ready. Say what to look for, or leave it blank, and choose Find moments.`);
    setStatus(`Loaded ${countOf(allCaptions.length, 'subtitle line')}`);
  } catch (err) {
    console.warn('Subtitles unavailable:', err);
    allCaptions = [];
    updateFindMomentsButton();
    showMessage(captionsList, `Subtitles could not be loaded. ${err.message}`, true);
    showMessage(highlightsList, 'Finding moments needs subtitles. You can still trim the range by hand.');
    setStatus('Subtitles failed');
  }
}

function renderCaptions(segments) {
  if (!segments || segments.length === 0) {
    showMessage(captionsList, allCaptions.length ? 'No lines match your search.' : NO_SUBTITLES);
    return;
  }
  captionsList.innerHTML = segments.map((s) => `
    <button class="transcript-line" data-seek="${s.start}">
      <span class="transcript-time">${formatTime(s.start)}</span>
      <span>${escapeHtml(s.text)}</span>
    </button>
  `).join('');
}

function seekVideo(time) {
  videoPlayer.currentTime = time;
  videoPlayer.play();
}

captionsList.addEventListener('click', (e) => {
  const line = e.target.closest('[data-seek]');
  if (line) seekVideo(parseFloat(line.dataset.seek));
});

searchCaptionsInput.addEventListener('input', (e) => {
  const term = e.target.value.toLowerCase();
  const filtered = allCaptions.filter(s => s.text.toLowerCase().includes(term));
  renderCaptions(filtered);
});

async function triggerAiHighlights() {
  if (allCaptions.length === 0) return;

  setStatus('Finding moments…', true);
  aiAnalyzeBtn.disabled = true;
  aiAnswered.hidden = true;
  highlightsList.innerHTML = `<p class="empty"><span class="spinner"></span> Reading the transcript…</p>`;

  try {
    await rememberAiSettings();
    const data = await findHighlights(aiRequestBody());
    const highlights = data.highlights || [];
    renderHighlights(highlights);
    aiAnswered.textContent = `Answered by ${providerLabel(data.provider)}`;
    aiAnswered.hidden = false;
    setStatus(`Found ${countOf(highlights.length, 'moment')}`);
  } catch (err) {
    console.error(err);
    showMessage(highlightsList, err.message, true);
    setStatus('Finding moments failed');
  } finally {
    updateFindMomentsButton();
  }
}

function aiRequestBody() {
  const body = { captions: allCaptions, provider: aiProviderSelect.value };
  const optional = [
    ['api_key', geminiApiKey, 'aiKeyRow'],
    ['model', aiModelInput, 'aiModelRow'],
    ['base_url', aiBaseUrlInput, 'aiBaseUrlRow'],
    ['query', aiQueryInput, null],
  ];
  for (const [name, input, rowId] of optional) {
    const value = input.value.trim();
    if (value && !(rowId && document.getElementById(rowId).hidden)) body[name] = value;
  }
  return body;
}

function updateFindMomentsButton() {
  aiAnalyzeBtn.disabled = allCaptions.length === 0;
}

function providerLabel(id) {
  return aiProviders.get(id)?.label || id;
}

async function loadAiProviders() {
  try {
    applyAiProviders(await listProviders());
  } catch (err) {
    console.warn(err);
    applyAiProviders({ providers: [], auto: null });
    aiProviderHint.textContent = 'AI providers could not be loaded. Automatic is the only choice.';
  }
}

function applyAiProviders(data) {
  const selected = aiProviderSelect.value === 'auto' ? aiPrefs.provider : aiProviderSelect.value;
  aiProviders = new Map(data.providers.map(p => [p.id, p]));
  aiAuto = data.auto;
  aiProviderSelect.replaceChildren(new Option(aiAuto ? `Automatic (${providerLabel(aiAuto)})` : 'Automatic', 'auto'));
  for (const p of aiProviders.values()) aiProviderSelect.add(new Option(p.label, p.id));
  if (aiProviders.has(selected)) aiProviderSelect.value = selected;
  renderAiFields();
}

async function rememberAiSettings() {
  const p = aiProviders.get(aiProviderSelect.value);
  if (!p?.accepts_key || !aiRememberInput.checked) return;
  const settings = {
    api_key: geminiApiKey.value.trim(),
    model: aiModelInput.value.trim(),
    base_url: p.uses_base_url ? aiBaseUrlInput.value.trim() : '',
  };
  if (!settings.api_key && !settings.model && !settings.base_url) return;
  applyAiProviders(await saveProviderSettings(p.id, settings));
}

aiForgetBtn.addEventListener('click', async () => {
  const id = aiProviderSelect.value;
  try {
    applyAiProviders(await forgetProviderSettings(id));
    aiPrefs.models[id] = '';
    aiPrefs.baseUrls[id] = '';
    saveAiPrefs(aiPrefs);
    renderAiFields();
    aiProviderHint.textContent = `Forgot the saved ${providerLabel(id)} settings.`;
  } catch (err) {
    aiProviderHint.textContent = err.message;
  }
});

function renderAiFields() {
  const id = aiProviderSelect.value;
  const p = aiProviders.get(id);
  document.getElementById('aiModelRow').hidden = !p;
  document.getElementById('aiKeyRow').hidden = !p?.accepts_key;
  aiRememberRow.hidden = !p?.accepts_key;
  aiForgetBtn.hidden = !p?.has_saved_settings;
  document.getElementById('aiBaseUrlRow').hidden = !p?.uses_base_url;
  geminiApiKey.value = '';
  if (p) {
    geminiApiKey.placeholder = p.saved_key_hint ? `Saved key ${p.saved_key_hint}`
      : p.has_server_key ? 'Uses the server key' : (p.needs_key ? 'API key' : 'API key (optional)');
    aiModelInput.placeholder = p.default_model || 'Model name';
    aiModelInput.value = aiPrefs.models[id] || '';
    aiBaseUrlInput.placeholder = p.default_base_url || 'Base URL';
    aiBaseUrlInput.value = aiPrefs.baseUrls[id] || '';
  }
  renderAiHint();
}

function renderAiHint() {
  const p = aiProviders.get(aiProviderSelect.value);
  if (!p && aiAuto) {
    aiProviderHint.textContent = `Automatic uses ${providerLabel(aiAuto)} with the server's settings.`;
  } else if (p?.accepts_key) {
    aiProviderHint.textContent = aiRememberInput.checked
      ? 'Saved on this computer when you choose Find moments, so you only enter it once.'
      : 'Sent with this request only, never saved.';
  } else if (p) {
    aiProviderHint.textContent = '';
  }
}

aiProviderSelect.addEventListener('change', () => {
  aiPrefs.provider = aiProviderSelect.value;
  saveAiPrefs(aiPrefs);
  renderAiFields();
});
aiModelInput.addEventListener('input', () => {
  aiPrefs.models[aiProviderSelect.value] = aiModelInput.value.trim();
  saveAiPrefs(aiPrefs);
});
aiBaseUrlInput.addEventListener('input', () => {
  aiPrefs.baseUrls[aiProviderSelect.value] = aiBaseUrlInput.value.trim();
  saveAiPrefs(aiPrefs);
});
aiRememberInput.addEventListener('change', renderAiHint);
aiQueryInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !aiAnalyzeBtn.disabled) triggerAiHighlights();
});

function renderHighlights(highlights) {
  if (!highlights || highlights.length === 0) {
    showMessage(highlightsList, 'No moments found in this transcript.');
    return;
  }

  highlightsList.innerHTML = highlights.map(h => `
    <article class="highlight">
      <div class="highlight-head">
        <h3 class="highlight-title">${escapeHtml(h.title)}</h3>
        <span class="highlight-score" title="Highlight score">${Math.round(h.score * 100)}%</span>
      </div>
      <p class="highlight-reason">${escapeHtml(h.reason)}</p>
      <div class="highlight-foot">
        <span class="highlight-range">${formatTime(h.start)}–${formatTime(h.end)}</span>
        <button class="btn btn-sm" data-start="${h.start}" data-end="${h.end}">Use this range</button>
      </div>
    </article>
  `).join('');
}

highlightsList.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-start]');
  if (btn) applyClipRange(parseFloat(btn.dataset.start), parseFloat(btn.dataset.end));
});

function applyClipRange(start, end) {
  startSlider.value = start;
  endSlider.value = end;
  // Sliders snap to their 0.1s step; the AI's timestamps must reach export unrounded.
  clipStart = start;
  clipEnd = end;
  renderRange();
  videoPlayer.currentTime = start;
  videoPlayer.play();
}

async function exportClip() {
  if (!currentVideoId) {
    alert('Load a video before exporting.');
    return;
  }

  exportBtn.disabled = true;
  const body = { video_id: currentVideoId, start_time: clipStart, end_time: clipEnd, captions: allCaptions };
  setStatus('Exporting clip…', true);

  try {
    const data = await createClip(body);
    setStatus('Clip exported');

    const a = document.createElement('a');
    a.href = data.download_url;
    a.download = data.file_name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } catch (err) {
    console.error(err);
    setStatus('Export failed: ' + err.message);
    alert(err.message);
  } finally {
    exportBtn.disabled = false;
  }
}

function showLoadError(text) {
  loadError.textContent = text || '';
  loadError.hidden = !text;
  urlInput.setAttribute('aria-invalid', text ? 'true' : 'false');
}

loadBtn.addEventListener('click', loadVideo);
fetchCcTopBtn.addEventListener('click', fetchCaptions);
aiAnalyzeBtn.addEventListener('click', triggerAiHighlights);
exportBtn.addEventListener('click', exportClip);

urlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') loadVideo();
});

loadAiProviders();

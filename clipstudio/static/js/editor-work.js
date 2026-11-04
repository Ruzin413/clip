/**
 * The editor's work in progress: the loaded video, its subtitles, the found moments, the range and the panel inputs.
 * It lives in this browser so moving to another page and back keeps it. Clear, a reload or a new link drops it.
 * API keys never go here.
 */
const WORK_KEY = 'clipStudio.editorWork';

export function readWork() {
  try {
    return JSON.parse(localStorage.getItem(WORK_KEY)) || null;
  } catch {
    return null;
  }
}

/** @returns {boolean} false when the browser refused it, for example when the subtitles are too big to store */
export function saveWork(work) {
  try {
    localStorage.setItem(WORK_KEY, JSON.stringify(work));
    return true;
  } catch {
    return false;
  }
}

export function forgetWork() {
  try {
    localStorage.removeItem(WORK_KEY);
  } catch {}
}

/** True when the person reloaded this page, which counts as asking for a fresh editor. */
export function pageWasReloaded() {
  return performance.getEntriesByType('navigation')[0]?.type === 'reload';
}

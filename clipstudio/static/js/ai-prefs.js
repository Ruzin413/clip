/** The AI provider, model and base URL per provider that the editor remembers in this browser. Keys never go here. */
const AI_PREFS_KEY = 'clipStudio.ai';

/** @returns {{provider: string|undefined, models: Record<string, string>, baseUrls: Record<string, string>}} */
export function readAiPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(AI_PREFS_KEY)) || {};
    return { provider: saved.provider, models: saved.models || {}, baseUrls: saved.baseUrls || {} };
  } catch {
    return { provider: undefined, models: {}, baseUrls: {} };
  }
}

export function saveAiPrefs(prefs) {
  try {
    localStorage.setItem(AI_PREFS_KEY, JSON.stringify(prefs));
  } catch {}
}

/** The AI choice for a run sent from outside the editor's AI panel. Saved keys stay on the server and apply there. */
export function aiChoiceFromPrefs(prefs = readAiPrefs()) {
  const provider = prefs.provider || 'auto';
  const choice = { provider };
  if (prefs.models[provider]) choice.model = prefs.models[provider];
  if (prefs.baseUrls[provider]) choice.base_url = prefs.baseUrls[provider];
  return choice;
}

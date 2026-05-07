// extension/panels/explainer.ts
// Pure DOM rendering for the Why-this-turn-matters card. Given a text (or
// null while waiting / on failure) and an isLoading flag, writes one of
// three states into the target element. No fetching, no state — caller
// drives refresh.

export function renderExplainer(target: HTMLElement, text: string | null, isLoading: boolean) {
  if (isLoading) {
    target.innerHTML = '<div class="sc-explainer-loading">analyzing turn…</div>';
    return;
  }
  if (!text) {
    target.innerHTML = '<div class="sc-empty">explanation unavailable (LLM down or engine still thinking)</div>';
    return;
  }
  target.innerHTML = `<div class="sc-explainer-text">${escapeHtml(text)}</div>`;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

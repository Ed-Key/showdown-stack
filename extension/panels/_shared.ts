/**
 * Shared utilities for the panels/ render modules. Kept in a leaf module
 * with no other deps so any panel can import safely.
 */

/**
 * HTML-escape arbitrary input for safe interpolation into innerHTML. Handles
 * non-string and nullish input (defensive against `any`-typed engine values).
 * Escapes the full set of significant HTML chars so the same function is
 * usable in text content AND attribute contexts.
 */
export function escapeHtml(s: any): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]!));
}

/**
 * Shared mount helper for the .sc-pinned panel children (TCG card, threats
 * panel, PV chain, conflict banner). Extracted from content.ts:renderUpdate
 * during Phase 4 — four near-identical replaceWith/insertBefore/appendChild
 * blocks collapsed into one helper.
 *
 * The Phase 1 mount-order test (test/lib/mount-order.test.ts) is the
 * regression net for this extraction.
 */

export interface MountAnchor {
  /** Selector for the anchor element to position relative to. */
  selector: string;
  /** Insert the new element before or after the anchor. */
  position: 'before' | 'after';
}

export interface MountOptions {
  /** The element to mount. */
  newEl: HTMLElement;
  /**
   * Selectors that, if found, are REPLACED by newEl in place (preserving
   * DOM position). Tried in order; first match wins. Idempotent re-mounts
   * use this — the existing panel instance gets swapped out.
   */
  replaceTargets: string[];
  /**
   * If no replaceTargets matched, try inserting relative to each anchor in
   * order; first match wins. Empty list = skip straight to fallback.
   */
  anchors?: MountAnchor[];
  /**
   * Called when neither replaceTargets nor anchors matched. The default
   * (when omitted) appends to the root element.
   */
  fallback?: (root: HTMLElement, newEl: HTMLElement) => void;
}

/**
 * Mount `newEl` inside `root` according to the strategy in `opts`. Single
 * entry point for all four panel mount sites in content.ts.
 *
 * Strategy: replace existing instance if present → otherwise position by
 * anchor → otherwise fallback. The first matching rule wins.
 */
export function mountOrReplace(root: HTMLElement, opts: MountOptions): void {
  for (const sel of opts.replaceTargets) {
    const existing = root.querySelector(sel);
    if (existing) {
      existing.replaceWith(opts.newEl);
      return;
    }
  }
  for (const anchor of opts.anchors ?? []) {
    const el = root.querySelector(anchor.selector);
    if (el && el.parentElement) {
      const ref = anchor.position === 'before' ? el : el.nextSibling;
      el.parentElement.insertBefore(opts.newEl, ref);
      return;
    }
  }
  if (opts.fallback) {
    opts.fallback(root, opts.newEl);
  } else {
    root.appendChild(opts.newEl);
  }
}

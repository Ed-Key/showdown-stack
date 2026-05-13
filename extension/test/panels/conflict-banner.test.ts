/** @vitest-environment jsdom */
import { describe, it, expect } from 'vitest';
import { renderConflictBanner } from '../../panels/conflict-banner';

describe('renderConflictBanner', () => {
  it('returns an element with the conflict-banner class', () => {
    const el = renderConflictBanner({
      severity: 'STRONG',
      reason: 'Engine recommends Ice Spinner but Zapdos OHKOs first.',
    });
    expect(el.classList.contains('sc-conflict-banner')).toBe(true);
  });

  it('renders the severity label', () => {
    const el = renderConflictBanner({
      severity: 'STRONG',
      reason: 'reason text',
    });
    expect(el.textContent).toContain('STRONG CONFLICT');
  });

  it('renders the reason text', () => {
    const el = renderConflictBanner({
      severity: 'POSSIBLE',
      reason: 'Maybe an OHKO if scarfed',
    });
    expect(el.textContent).toContain('Maybe an OHKO if scarfed');
    expect(el.textContent).toContain('POSSIBLE CONFLICT');
  });

  it('escapes HTML in the reason text', () => {
    const el = renderConflictBanner({
      severity: 'STRONG',
      reason: '<script>alert(1)</script>',
    });
    expect(el.innerHTML).not.toContain('<script>');
    expect(el.textContent).toContain('<script>alert(1)</script>');
  });

  it('returns a hidden element when severity is null', () => {
    const el = renderConflictBanner(null);
    expect(el.classList.contains('hidden')).toBe(true);
  });
});

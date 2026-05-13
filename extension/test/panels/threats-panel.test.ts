/** @vitest-environment jsdom */
import { describe, it, expect } from 'vitest';
import { renderThreatsPanel, type ThreatsPanelProps } from '../../panels/threats-panel';

const FIXTURE: ThreatsPanelProps = {
  onField: [
    { move: 'Ceaseless Edge', source: 'Samurott-Hisui', target: 'Iron Valiant', dmgPct: 214, isOhko: true, source_seen: true },
    { move: 'Sucker Punch',   source: 'Samurott-Hisui', target: 'Iron Valiant', dmgPct: 89,  isOhko: false, source_seen: false },
  ],
  incoming: [
    { move: 'Hurricane', source: 'Zapdos', target: 'Iron Valiant', dmgPct: 128, isOhko: false, source_seen: false },
  ],
};

describe('renderThreatsPanel', () => {
  it('returns an element with the trainer-card class', () => {
    const el = renderThreatsPanel(FIXTURE);
    expect(el.classList.contains('sc-trainer-card')).toBe(true);
  });

  it('renders ON-FIELD section with all threats', () => {
    const el = renderThreatsPanel(FIXTURE);
    expect(el.textContent).toContain('ON-FIELD');
    expect(el.textContent).toContain('Ceaseless Edge');
    expect(el.textContent).toContain('Sucker Punch');
  });

  it('renders INCOMING section', () => {
    const el = renderThreatsPanel(FIXTURE);
    expect(el.textContent).toContain('INCOMING');
    expect(el.textContent).toContain('Hurricane');
  });

  it('marks OHKO threats with the ohko icon and class', () => {
    const el = renderThreatsPanel(FIXTURE);
    const ohkoIcons = el.querySelectorAll('.threat-icon.ohko');
    expect(ohkoIcons.length).toBeGreaterThan(0);
    expect(ohkoIcons[0].textContent).toBe('☠');
  });

  it('shows SEEN tag for revealed moves and CHAOS tag otherwise', () => {
    const el = renderThreatsPanel(FIXTURE);
    expect(el.querySelector('.seen-tag.seen')).not.toBeNull();
    expect(el.querySelector('.seen-tag.chaos')).not.toBeNull();
  });

  it('renders damage percentages', () => {
    const el = renderThreatsPanel(FIXTURE);
    expect(el.textContent).toContain('214%');
    expect(el.textContent).toContain('128%');
    expect(el.textContent).toContain('89%');
  });

  it('returns an empty panel when no threats', () => {
    const el = renderThreatsPanel({ onField: [], incoming: [] });
    expect(el.querySelectorAll('.threat-row').length).toBe(0);
  });
});

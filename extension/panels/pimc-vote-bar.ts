// extension/panels/pimc-vote-bar.ts
// Pure DOM rendering for the PIMC vote-bar card. When the engine response
// includes a `pimcBreakdown` field (only present when the proxy is in PIMC
// mode, i.e. POKE_PROXY_PIMC_K > 0), render the per-hypothesis vote
// distribution: how many of K hypotheses voted for each top_move, the mean
// visit_share for that move, and which opp_summary strings produced each
// vote. When all K agree on one move, this is a strong consensus signal;
// when they disagree it's a hedged recommendation worth eyeballing.
//
// Degrades gracefully: missing/empty input → "no PIMC data" stub.
// Per-hypothesis records may be missing fields (top_move, value,
// visit_share, opp_summary) — defaults applied so a malformed proxy event
// can never crash the panel.

export type PimcHypothesis = {
  top_move?: string;
  value?: number;
  visit_share?: number;
  opp_summary?: string;
};

export type VoteRow = {
  move: string;
  votes: number;
  meanVisitShare: number;
  oppSummaries: string[];
};

/** Aggregate raw hypothesis records into one row per unique top_move. */
export function aggregateVotes(breakdown: PimcHypothesis[]): VoteRow[] {
  if (!Array.isArray(breakdown) || breakdown.length === 0) return [];
  const buckets = new Map<string, { shares: number[]; opps: string[] }>();
  for (const h of breakdown) {
    const move = (h && typeof h.top_move === 'string' && h.top_move) || '(unknown)';
    const share = typeof h?.visit_share === 'number' && Number.isFinite(h.visit_share)
      ? h.visit_share : 0;
    const opp = (h && typeof h.opp_summary === 'string' && h.opp_summary) || '(unknown opp)';
    const b = buckets.get(move) ?? { shares: [], opps: [] };
    b.shares.push(share);
    b.opps.push(opp);
    buckets.set(move, b);
  }
  const rows: VoteRow[] = [];
  for (const [move, b] of buckets.entries()) {
    const mean = b.shares.reduce((s, v) => s + v, 0) / b.shares.length;
    rows.push({ move, votes: b.shares.length, meanVisitShare: mean, oppSummaries: b.opps });
  }
  rows.sort((a, b) => b.votes - a.votes || b.meanVisitShare - a.meanVisitShare);
  return rows;
}

export function renderPimcVoteBar(
  target: HTMLElement,
  breakdown: PimcHypothesis[] | null | undefined,
  bestMove: string | null | undefined,
): void {
  if (!Array.isArray(breakdown) || breakdown.length === 0) {
    target.innerHTML = '<div class="sc-empty">no PIMC data (single-modal response)</div>';
    return;
  }
  const rows = aggregateVotes(breakdown);
  const k = breakdown.length;
  const top = rows[0];
  const consensusMove = (typeof bestMove === 'string' && bestMove) || top.move;
  const agree = rows.find(r => r.move === consensusMove)?.votes ?? top.votes;
  const split = agree < k;

  const headerCls = split ? 'sc-pimc-header sc-pimc-split' : 'sc-pimc-header';
  const splitTag = split ? '<span class="sc-pimc-split-tag">⚠ split decision</span>' : '';

  const tableRows = rows.map(r => {
    const isConsensus = r.move === consensusMove;
    const cls = isConsensus ? 'sc-pimc-row sc-pimc-row-top' : 'sc-pimc-row';
    const sharePct = (Math.max(0, Math.min(1, r.meanVisitShare)) * 100).toFixed(0);
    const opps = r.oppSummaries.slice(0, 3).map(o => truncate(o, 36)).join(' · ');
    const moreOpps = r.oppSummaries.length > 3 ? ` (+${r.oppSummaries.length - 3} more)` : '';
    return `
      <div class="${cls}">
        <div class="sc-pimc-row-line">
          <span class="sc-pimc-move">${escapeHtml(r.move)}</span>
          <span class="sc-pimc-votes">${r.votes}/${k}</span>
          <span class="sc-pimc-share">${sharePct}%</span>
        </div>
        <div class="sc-pimc-opps" title="${escapeHtml(r.oppSummaries.join(' | '))}">${escapeHtml(opps)}${escapeHtml(moreOpps)}</div>
      </div>
    `;
  }).join('');

  target.innerHTML = `
    <div class="${headerCls}">
      <span class="sc-pimc-summary">${agree} of ${k} hypotheses agree on: <b>${escapeHtml(consensusMove)}</b></span>
      <span class="sc-pimc-badge">PIMC: K=${k}</span>
      ${splitTag}
    </div>
    <div class="sc-pimc-table">${tableRows}</div>
  `;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + '…';
}

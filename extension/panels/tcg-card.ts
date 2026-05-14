/**
 * Render the main TCG-styled recommendation card.
 * Pure: takes data, returns a DOM element. No side effects, no global state.
 */
import { TCG_TYPE_MAP } from '../lib/tcg/types';
import { ENERGY_PALETTE } from '../lib/tcg/energy-orbs';
import { speciesToSpriteURL } from '../lib/tcg/species-url';
import type { TrendArrow } from '../lib/val-trend';
import { escapeHtml } from './_shared';

export interface AlternativeMove {
  name: string;
  /** Showdown type (e.g. "Ice", "Fairy", "Normal", "Switch") */
  type: string;
  votes: number;
  voteCap: number;
  winPct: number;
  /** 'P' physical, 'S' special, 'T' status/switch */
  category: 'P' | 'S' | 'T';
  isRecommended: boolean;
  desc: string;
}

export interface TcgCardProps {
  /** Showdown primary type of the active Pokemon — drives the frame color. */
  activeType: string;
  trend: TrendArrow;
  /** Pokemon currently on your side of the field. */
  activeSpecies: string;
  /**
   * Pokemon shown in the top header strip + art frame: defaults to
   * activeSpecies, but swaps to the switch target when the engine
   * recommends a switch (so the user sees who they're being asked to bring
   * in). When isSwitchRec is true, this is the target species.
   */
  headerSpecies: string;
  /** True when bestMove is a Pokemon name (switch), not a move. */
  isSwitchRec: boolean;
  turn: number;
  trendTag: string;
  hypsTag: string;
  winPct: number;
  llmExplanation: string;
  /** Includes the recommended move as the first entry (isRecommended:true). */
  moves: AlternativeMove[];
  worstThreat: { name: string; dmgPct: number; isOhko: boolean };
  retreatCost: number;
  /** Win% history (0-1 scale) for sparkline */
  sparklineHistory: number[];
  flairChar: string;
}

/** Build energy orb element (image or SVG depending on tcg type) */
function buildOrb(tcgType: string, filled: boolean): HTMLElement {
  const el = document.createElement('div');
  el.className = filled ? 'energy' : 'energy empty';
  if (!filled) return el;

  const config = ENERGY_PALETTE[tcgType] ?? ENERGY_PALETTE.colorless;
  if (config.src === 'img') {
    el.style.backgroundImage = `url('${config.url}')`;
    el.style.backgroundSize = config.bg;
    el.style.backgroundPosition = config.pos;
    el.style.backgroundRepeat = 'no-repeat';
  } else {
    // SVG path — inline an SVG instead of background
    el.innerHTML = `
      <svg viewBox="0 0 100 100" width="22" height="22">
        <defs>
          <radialGradient id="cl-sphere-orb" cx="32%" cy="28%">
            <stop offset="0%" stop-color="#ffffff"/>
            <stop offset="40%" stop-color="#c8c8d0"/>
            <stop offset="100%" stop-color="#606870"/>
          </radialGradient>
        </defs>
        <circle cx="50" cy="50" r="46" fill="url(#cl-sphere-orb)" stroke="#303040" stroke-width="3"/>
        <path d="${config.path}" fill="#1a1a24"/>
        <ellipse cx="35" cy="30" rx="14" ry="8" fill="rgba(255,255,255,0.5)"/>
      </svg>
    `;
    el.style.background = 'transparent';
    el.style.boxShadow = 'none';
  }
  return el;
}

function buildSparkline(history: number[]): SVGElement {
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', '0 0 200 22');
  svg.setAttribute('preserveAspectRatio', 'none');
  if (history.length < 2) return svg;
  const stepX = 200 / (history.length - 1);
  const pts = history.map((v, i) => `${i * stepX},${22 - v * 22}`).join(' ');
  const poly = document.createElementNS(svgNS, 'polyline');
  poly.setAttribute('points', pts);
  poly.setAttribute('fill', 'none');
  poly.setAttribute('stroke', '#fff');
  poly.setAttribute('stroke-width', '2');
  poly.setAttribute('stroke-linecap', 'round');
  svg.appendChild(poly);
  const lastY = 22 - history[history.length - 1] * 22;
  const dot = document.createElementNS(svgNS, 'circle');
  dot.setAttribute('cx', '200');
  dot.setAttribute('cy', String(lastY));
  dot.setAttribute('r', '3');
  dot.setAttribute('fill', '#fff');
  svg.appendChild(dot);
  return svg;
}

/** Format set/card-number flair footer (neutral — no tcgdex API in v1A) */
function formatFooter(turn: number): { left: string; right: string } {
  return {
    left: 'COPILOT · v1',
    right: `T${turn} · ◆`,
  };
}

export function renderTcgCard(p: TcgCardProps): HTMLElement {
  // Frame color follows the active Pokemon's primary type (real-TCG behavior):
  // stays put until you switch, instead of jumping every turn with the
  // recommendation. Per-move orb colors still reflect each move's own type.
  const tcgType = TCG_TYPE_MAP[p.activeType] ?? 'colorless';
  const card = document.createElement('div');
  card.className = `sc-tcg-card t-${tcgType}`;

  // Active sprite drives the art frame; header sprite swaps to the switch
  // target when the engine recommends a switch (so the user sees who's
  // coming in at a glance) and otherwise mirrors the active Pokemon.
  const activeSpriteURL = speciesToSpriteURL(p.activeSpecies);
  const headerSpriteURL = speciesToSpriteURL(p.headerSpecies);
  const deltaStr = p.trend.delta >= 0 ? `+${p.trend.delta}` : `${p.trend.delta}`;
  const footer = formatFooter(p.turn);
  const stageLabel = p.isSwitchRec ? `SWITCH TO · T${p.turn}` : `ACTIVE · T${p.turn}`;

  const html = `
    <div class="inner">
      <div class="top">
        <div class="poke-mini"><img alt="header" src="${headerSpriteURL}"/></div>
        <div class="name-wrap">
          <div class="stage-label">${stageLabel}</div>
          <div class="card-name">${escapeHtml((p.headerSpecies || '').toUpperCase())}</div>
        </div>
        <div class="trend-slot">
          <span class="trend-arrow" style="color:${p.trend.color}">${p.trend.arrow}</span>
          <span class="trend-delta" style="color:${p.trend.color}">${deltaStr}</span>
          <span class="type-pip-slot"></span>
        </div>
      </div>
      <div class="art-outer">
        <div class="art">
          <div class="art-tags">
            <div class="art-tag">${escapeHtml(p.trendTag)}</div>
            <div class="art-tag">${escapeHtml(p.hypsTag)}</div>
          </div>
          <span class="flair" style="top:14px;left:14px;font-size:14px">${p.flairChar}</span>
          <span class="flair" style="top:26px;right:16px;font-size:18px;animation-delay:-1.5s">${p.flairChar}</span>
          <span class="flair" style="bottom:50px;left:12px;font-size:14px;animation-delay:-2.5s">${p.flairChar}</span>
          <span class="flair" style="top:70px;left:70%;font-size:12px;animation-delay:-3.5s">${p.flairChar}</span>
          <span class="flair" style="bottom:80px;right:24px;font-size:16px;animation-delay:-4s">${p.flairChar}</span>
          <div class="sprite-stage"><img alt="" src="${activeSpriteURL}"/></div>
          <div class="spark-strip">
            <span class="spark-slot"></span>
            <div class="big-conf">${p.winPct}<span class="pct">%</span></div>
          </div>
        </div>
      </div>
      <div class="flavor">
        <span class="ai-tag">AI</span>"${escapeHtml(p.llmExplanation)}"
      </div>
      <div class="moves"></div>
      <div class="bottom">
        <div class="bottom-cell">
          <span class="bottom-label">WEAKNESS</span>
          <span class="bottom-val">${p.worstThreat.isOhko ? '☠' : '⚠'} ${p.worstThreat.dmgPct}%</span>
        </div>
        <div class="bottom-cell">
          <span class="bottom-label">FROM</span>
          <span class="bottom-val">${escapeHtml(p.worstThreat.name.toUpperCase())}</span>
        </div>
        <div class="bottom-cell">
          <span class="bottom-label">RETREAT</span>
          <span class="bottom-val">⬇ ${p.retreatCost}</span>
        </div>
      </div>
      <div class="footer">
        <span>${footer.left}</span>
        <span>${footer.right}</span>
      </div>
    </div>
  `;
  card.innerHTML = html;

  // Inject sparkline into the .spark-slot placeholder
  const sparkSlot = card.querySelector('.spark-slot') as HTMLElement | null;
  if (sparkSlot) {
    const spark = buildSparkline(p.sparklineHistory);
    sparkSlot.replaceWith(spark);
  }

  // Type pip in the header trend-slot uses the same energy orb asset as the
  // move rows (e.g. Gholdengo → Metal energy card crop), so the top-right
  // matches the per-row orbs visually.
  const pipSlot = card.querySelector('.type-pip-slot') as HTMLElement | null;
  if (pipSlot) {
    const orb = buildOrb(tcgType, true);
    orb.classList.add('type-pip');
    pipSlot.replaceWith(orb);
  }

  // Build the .moves rows — recommended move is moves[0], rendered with .rec
  // highlight; the remaining rows are the engine's top alternatives.
  const movesDiv = card.querySelector('.moves')!;
  for (const m of p.moves) {
    movesDiv.appendChild(buildMoveRow(m));
  }

  return card;
}

function buildMoveRow(alt: AlternativeMove): HTMLElement {
  const row = document.createElement('div');
  row.className = 'move' + (alt.isRecommended ? ' rec' : '');

  const energyRow = document.createElement('div');
  energyRow.className = 'energy-row';
  const altTcgType = TCG_TYPE_MAP[alt.type] ?? 'colorless';
  for (let i = 0; i < alt.voteCap; i++) {
    energyRow.appendChild(buildOrb(altTcgType, i < alt.votes));
  }
  row.appendChild(energyRow);

  const body = document.createElement('div');
  body.className = 'move-body';
  body.innerHTML = `
    <div class="move-top">
      ${alt.isRecommended ? '<span class="rec-star">★</span>' : ''}
      <span class="move-name">${escapeHtml(alt.name)}</span>
      <span class="cat ${alt.category.toLowerCase()}">${alt.category}</span>
    </div>
    <div class="move-desc">${alt.votes}/${alt.voteCap} votes · ${escapeHtml(alt.desc)}</div>
  `;
  row.appendChild(body);

  const dmg = document.createElement('div');
  dmg.className = 'move-dmg';
  dmg.textContent = String(alt.winPct);
  row.appendChild(dmg);

  return row;
}

import type { BattleSummary } from '../types';
import { fmtPct } from '../lib/format';

interface BattleListProps {
  battles: BattleSummary[];
}

export function BattleList({ battles }: BattleListProps) {
  return (
    <section className="section-panel battle-panel">
      <div className="section-head">
        <div>
          <h2>Recent Battles</h2>
          <p>Lower-priority review surface while team performance stays primary.</p>
        </div>
      </div>
      <div className="battle-list">
        {battles.slice(0, 8).map((battle) => (
          <article className="battle-row" key={battle.battleId}>
            <div>
              <h3>vs {battle.opponent}</h3>
              <p>{battle.endedAtLabel} / {battle.totalTurns} turns</p>
            </div>
            <div className="battle-row-stats">
              <span className={`result-pill ${battle.result}`}>{battle.result}</span>
              <span>{fmtPct(battle.metrics.followRate)}</span>
              <span>{battle.metrics.switchRecommendations} switches</span>
              <span>{battle.metrics.criticalTurns} critical</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

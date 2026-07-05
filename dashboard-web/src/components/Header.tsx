import type { DashboardArchive, TeamProfile } from '../types';
import { fmtPct, labelOrFallback } from '../lib/format';
import { PokemonSprite } from './PokemonSprite';
import { ShuffleTitle } from './ShuffleTitle';

interface HeaderProps {
  archive: DashboardArchive | null;
  team: TeamProfile | null;
}

export function Header({ archive, team }: HeaderProps) {
  const summary = archive?.summary;
  const title = labelOrFallback(team?.teamName, 'Showdown Copilot');
  const roster = team?.team || [];

  return (
    <header className="app-header">
      <div className="header-copy">
        <div className="kicker">Team Command Center</div>
        <h1><ShuffleTitle text={title} /></h1>
        <p>
          {summary
            ? `${summary.finishedBattles} tracked battles. ${summary.wins}W / ${summary.losses}L with ${fmtPct(summary.followRate)} follow rate.`
            : 'Loading local postmortem archive from the proxy.'}
        </p>
      </div>
      <div className="roster-strip" aria-label="Current roster">
        {roster.map((species) => (
          <div className="roster-sprite" key={species} title={species}>
            <PokemonSprite species={species} />
          </div>
        ))}
      </div>
    </header>
  );
}

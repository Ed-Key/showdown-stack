import type { PokemonProfile, TeamProfile } from '../types';
import { fmtPct } from '../lib/format';
import { PokemonSprite } from './PokemonSprite';

interface TeamPerformanceProps {
  team: TeamProfile | null;
}

function PokemonCard({ pokemon }: { pokemon: PokemonProfile }) {
  const faintLabel = pokemon.avgFaintTurn == null ? 'Alive' : `T${pokemon.avgFaintTurn}`;
  const primaryStat = pokemon.leadRate && pokemon.leadRate >= 35
    ? { label: 'Lead', value: fmtPct(pokemon.leadRate) }
    : { label: 'Survival', value: fmtPct(pokemon.survivalRate) };

  return (
    <article className="pokemon-card">
      <div className="pokemon-card-head">
        <div className="pokemon-portrait">
          <PokemonSprite species={pokemon.species} />
        </div>
        <div>
          <h3>{pokemon.species}</h3>
          <p>{pokemon.battles} battle{pokemon.battles === 1 ? '' : 's'} tracked</p>
        </div>
      </div>
      <div className="pokemon-tags">
        {pokemon.leadRate && pokemon.leadRate >= 35 ? <span>Lead</span> : null}
        {pokemon.survivalRate && pokemon.survivalRate >= 60 ? <span>Endgame</span> : null}
        {pokemon.koCreditTotal ? <span>KO {pokemon.koCreditTotal}</span> : null}
        <span>{pokemon.fieldPressureBucket} pressure</span>
      </div>
      <div className="pokemon-grid">
        <Metric label={primaryStat.label} value={primaryStat.value} />
        <Metric label="Avg Faint" value={faintLabel} />
        <Metric label="KO Share" value={fmtPct(pokemon.koShare)} />
        <Metric label="Chip Taken" value={fmtPct(pokemon.avgFieldPressureTakenPct)} />
      </div>
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function TeamPerformance({ team }: TeamPerformanceProps) {
  if (!team) {
    return (
      <section className="section-panel">
        <h2>Team Performance</h2>
        <div className="empty-state">No finished battles are available yet.</div>
      </section>
    );
  }

  return (
    <section className="section-panel team-performance-panel">
      <div className="section-head">
        <div>
          <h2>Team Performance</h2>
          <p>{team.team.join(' / ')}</p>
        </div>
        <div className="section-pills">
          <span>{team.battles} battles</span>
          <span>{fmtPct(team.winRate)} win</span>
          <span>{fmtPct(team.followRate)} follow</span>
          {team.topLead ? <span>Lead: {team.topLead.species} {fmtPct(team.topLead.rate)}</span> : null}
        </div>
      </div>
      <div className="pokemon-card-grid">
        {team.pokemon.map((pokemon) => (
          <PokemonCard key={pokemon.species} pokemon={pokemon} />
        ))}
      </div>
    </section>
  );
}

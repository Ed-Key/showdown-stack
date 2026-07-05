import { useState } from 'react';
import { pokemonSpriteUrl } from '../lib/pokemon';

interface PokemonSpriteProps {
  species: string;
  className?: string;
}

function initials(species: string): string {
  return species
    .split(/[\s-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('') || '?';
}

export function PokemonSprite({ species, className }: PokemonSpriteProps) {
  const [fallback, setFallback] = useState(false);
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <span className={`sprite-fallback ${className || ''}`} aria-label={species} title={species}>
        {initials(species)}
      </span>
    );
  }

  return (
    <img
      className={className}
      src={pokemonSpriteUrl(species, fallback)}
      alt={species}
      title={species}
      onError={() => {
        if (!fallback) setFallback(true);
        else setFailed(true);
      }}
    />
  );
}

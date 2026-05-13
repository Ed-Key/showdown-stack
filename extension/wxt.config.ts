import { defineConfig } from 'wxt';

export default defineConfig({
  manifest: {
    name: 'Showdown Copilot',
    description: 'Live MCTS advice panel for Pokémon Showdown battles',
    version: '0.2.0',
    host_permissions: [
      'https://play.pokemonshowdown.com/*',
      'http://localhost:7271/*',
      'https://assets.tcgdex.net/*',
      'https://fonts.googleapis.com/*',
      'https://fonts.gstatic.com/*',
    ],
  },
});

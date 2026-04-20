import { defineConfig } from 'wxt';

export default defineConfig({
  manifest: {
    name: 'Showdown Copilot',
    description: 'Live MCTS advice panel for Pokémon Showdown battles',
    version: '0.2.0',
    host_permissions: [
      'http://localhost:7267/*',
      'http://127.0.0.1:7267/*',
    ],
  },
});

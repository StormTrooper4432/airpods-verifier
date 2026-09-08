import { defineConfig } from 'astro/config';
import node from '@astrojs/node';

export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
  server: { port: 4321, host: true },
  // Replaced by src/middleware.ts, which reconstructs the expected origin from
  // the proxy's forwarded headers. Astro's own check compares against the URL
  // the server sees — behind a load balancer that is http://localhost:8080, so
  // every form post from the real origin is refused as cross-site.
  security: { checkOrigin: false },
  devToolbar: { enabled: false },
});

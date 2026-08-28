import path from "node:path";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// The URL every asset in this bundle is served from. It has to be ABSOLUTE, and that is not a
// style preference: www/folt.py serves the same document at /folt and at /folt/<any>/<deep>/<link>
// (hooks.website_route_rules), so a relative base would resolve to /folt/any/deep/static/... and
// 404 on deep links only -- which is to say, only after somebody bookmarks one.
const BASE = "/assets/folt_customizations/folt/";

export default defineConfig({
  base: BASE,
  plugins: [vue()],

  // No public/ passthrough. Nothing un-hashed may land under folt/static/, because the immutable
  // cache rule in erpnext-docker/docker/nginx/nginx.conf.template is scoped to that path prefix
  // rather than to a hash pattern -- see the comment there. publicDir:false plus [hash] on all
  // three output names below is what makes "everything under folt/static/ is content-hashed" a
  // property of the build rather than a thing to remember.
  publicDir: false,

  build: {
    // INSIDE the app's public/ dir, and this is the load-bearing decision of the whole setup.
    // images/layered/Containerfile links each app's public/ into sites/assets/<app>, guarded by
    // `[ ! -e "sites/assets/$app" ]`. A real directory at that path would skip the symlink and
    // take folt_branding.css, folt_desk.css, the logos and the desktop icons with it -- all served
    // at stable non-hashed paths named in hooks.py. Emitting in here means nothing new ever
    // appears at sites/assets/folt_customizations, so one symlink keeps serving both the plain
    // files and this bundle. No guard to change, no ordering hazard.
    outDir: path.resolve(import.meta.dirname, "../folt_customizations/public/folt"),
    emptyOutDir: true, // required: outDir is outside the vite root
    manifest: "manifest.json", // at outDir root, not .vite/manifest.json
    // No mime type for .map in frappe's nginx, so a sourcemap is neither gzipped nor usefully
    // cached, and it would ship the whole frontend source under a world-readable /assets path.
    sourcemap: false,
    rollupOptions: {
      // No index.html: www/folt.html is the shell and frappe renders it.
      input: path.resolve(import.meta.dirname, "src/main.ts"),
      output: {
        entryFileNames: "static/[name]-[hash].js",
        chunkFileNames: "static/[name]-[hash].js",
        assetFileNames: "static/[name]-[hash][extname]",
      },
    },
  },

  server: {
    port: 5173,
    strictPort: true,
    // The document is served by frappe on :8080 and only the modules come from here, so the dev
    // server has to emit absolute URLs and allow the cross-origin module requests that follow.
    // Browsing :5173 directly is deliberately not the workflow: the site's host_name is
    // localhost:8080, so every server-generated link and the login redirect would bounce you off
    // the dev origin. See www/folt.py:_dev_server.
    origin: "http://localhost:5173",
    cors: { origin: "http://localhost:8080" },
  },
});

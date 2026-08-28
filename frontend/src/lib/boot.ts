// What the server told us at render time, read once.
//
// www/folt.py renders this into a <script type="application/json"> rather than making the SPA
// fetch it: the session, the roles and the CSRF token are all known while the page is being
// rendered, and a first paint that has to wait for a round trip to learn who you are is a first
// paint nobody wants. The page is no_cache, so this can never be another user's token.

export type Boot = {
  user: string;
  full_name: string;
  roles: string[];
  csrf_token: string;
  asset_base: string;
  app_path: string;
};

function read(): Boot {
  const node = document.getElementById("folt-boot");
  if (!node?.textContent) {
    // Only reachable if the page was served by something other than www/folt.py -- worth failing
    // loudly, because every call below would otherwise fail one at a time on a missing token.
    throw new Error("no #folt-boot payload: this bundle is not being served by www/folt.py");
  }
  return JSON.parse(node.textContent) as Boot;
}

export const boot = read();

"""The document that boots the FoLT single-page app.

Served at /folt, and at /folt/<anything> through the `website_route_rules` entry in hooks.py --
one route rule, because frappe resolves the bare path by filename (www/folt.html) and 404s
everything below it otherwise. The SPA router owns whatever follows.

WHY THE ASSET NAMES ARE READ AT REQUEST TIME. The usual frappe-ui arrangement lets Vite overwrite
this page's HTML with the hashed filenames baked in. That is the one artifact this stack must not
produce: scripts/dokploy-post-deploy.sh:77-90 documents at length what happens when HTML naming
content hashes outlives the image it was built from -- every hashed asset 404s and the app renders
unstyled, and restarting redis does not fix it because the stale keys reload from the RDB. A
manifest read here, on a page that is never cached, cannot name a hash that is absent from the
image it is running in. The trade is one gunicorn render per page load, which is also what buys
the Guest redirect and the CSRF token.
"""

import json

import frappe
from frappe import _
# Imported explicitly rather than reached as `frappe.sessions.get_csrf_token`. frappe's own
# www/desk.py does the latter and gets away with it because something else has already imported
# the submodule by the time a Desk request runs -- `frappe.sessions` is not bound on the package
# otherwise, and it is not in a `bench execute`.
from frappe.sessions import get_csrf_token

# Where the bundle is served from. Inside the app's public/ dir on purpose -- see
# frontend/vite.config.ts for why that placement is load-bearing rather than arbitrary.
ASSET_BASE = "/assets/folt_customizations/folt/"

# The Vite entry, as it appears as a manifest key.
ENTRY = "src/main.ts"

# Read by frappe itself as a module-level flag, in addition to context.no_cache below.
no_cache = 1


def get_context(context):
	# A kill switch that needs no rebuild. A Dokploy deploy is a ~20 minute round trip, so the only
	# useful lever during an incident is one that lives in site config:
	#   bench --site <site> set-config folt_spa_disabled 1 && bench --site <site> clear-cache
	if frappe.conf.get("folt_spa_disabled"):
		raise frappe.DoesNotExistError

	# Three independent reasons, any one of which would be enough:
	#   1. csrf_token below is per session, and get_csrf_token() writes it into the session. A
	#      cached render hands one user another user's token.
	#   2. the <script src> names a content hash that changes with every image (see the module
	#      docstring).
	#   3. the manifest is read from disk here, so caching the render would defeat the read.
	# Precedent on this app: templates/pages/rfq.py sets the same flag.
	context.no_cache = 1

	if frappe.session.user == "Guest":
		# rstrip("?"): werkzeug's full_path always appends the separator, so a query-less request
		# yields "/folt?" and the user comes back to a URL with a stray question mark on it.
		target = (frappe.request.full_path or frappe.request.path).rstrip("?")
		frappe.local.flags.redirect_location = "/login?redirect-to=" + frappe.utils.quoted(target)
		raise frappe.Redirect

	context.csrf_token = get_csrf_token()
	context.scripts, context.styles = _assets()

	# One payload rather than three round trips before the first paint. Everything here is already
	# known while this page renders, and none of it is a secret the session does not already hold.
	# NOTHING SENSITIVE GOES IN HERE: it is rendered into HTML and, in dev, sits beside a bundle
	# served from a world-readable /assets path.
	context.boot = json.dumps(
		{
			"user": frappe.session.user,
			"full_name": frappe.utils.get_fullname(frappe.session.user),
			"roles": frappe.get_roles(),
			"csrf_token": context.csrf_token,
			"asset_base": ASSET_BASE,
			# The tail of a deep link, handed over by the route rule so the SPA router can pick it
			# up without re-parsing window.location.
			"app_path": frappe.form_dict.get("app_path") or "",
		}
	).replace("</", "<\\/")  # `</script` is the only sequence that can end the element early


def _assets() -> tuple[list[str], list[str]]:
	"""(scripts, styles) for this request: the dev server if one is configured, else the manifest."""
	dev_server = (frappe.conf.get("folt_vite_dev_server") or "").rstrip("/")
	if dev_server:
		# Local HMR. Set per developer, and it lives in site_config.json inside the sites volume, so
		# it can never ship in an image:
		#   bench --site folt.localhost set-config folt_vite_dev_server http://localhost:5173
		# The document still comes from frappe on :8080 -- one origin for the cookie, the CSRF token
		# and socket.io -- and only the modules come from Vite. See frontend/vite.config.ts.
		return [f"{dev_server}/@vite/client", f"{dev_server}/{ENTRY}"], []

	entry = _manifest().get(ENTRY)
	if not entry:
		frappe.throw(
			_("The FoLT frontend manifest has no entry for {0}.").format(ENTRY),
			title=_("FoLT frontend build is stale"),
		)

	return [ASSET_BASE + entry["file"]], [ASSET_BASE + href for href in entry.get("css") or []]


def _manifest() -> dict:
	"""Vite's manifest, read from apps/ rather than from sites/assets.

	This code runs in `backend`, which renders the HTML; `frontend` serves the files. They agree
	only because build.yml publishes ONE artifact under seven names -- the same invariant frappe's
	own assets.json rests on. The local dev loop is where that can be broken by hand: copying the
	build into folt-frontend-1 and forgetting folt-backend-1 makes this name hashes the server
	cannot serve.
	"""
	path = frappe.get_app_path("folt_customizations", "public", "folt", "manifest.json")
	try:
		with open(path) as handle:
			return json.load(handle)
	except FileNotFoundError:
		frappe.throw(
			_(
				"This image was built without the FoLT frontend build step, so there is no {0}. "
				"Rebuild it, or set <code>folt_vite_dev_server</code> to run against a local Vite."
			).format(path),
			title=_("FoLT frontend assets missing"),
		)

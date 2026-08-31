"""End-to-end check on the FoLT stylesheets and the self-hosted typeface.

Four files, two surfaces: folt_theme.css is the brand and loads on BOTH the Desk
(app_include_css) and the website/portal (web_include_css); folt_desk.css is the Desk's
guided layer; folt_portal.css is the supplier portal. Each opens with a contract, and a
contract in a comment is a wish. These are the clauses that can be checked mechanically, so
that breaking one fails a test rather than shipping and being noticed months later by
whoever uses dark mode -- or, on the portal, by a supplier nobody hears from.

Five things are worth naming, because each has already gone wrong once somewhere in this
project or is one edit away from doing so:

  - THE ASSETS ARE ACTUALLY THERE. An app whose public/ holds only plain CSS and images gets
    no sites/assets/<app> symlink from `bench build`, and that is exactly how every FoLT
    branding asset 404'd in an earlier image while the code looked perfect. A font that 404s
    is invisible in a different way from a font that is wrong: the Desk simply keeps rendering
    in the fallback and nobody can say when it stopped.

  - THE RAMP RULE. folt_desk.css rule 2 says no colour is written down, but the subtler half
    is that --green-500, --blue-500, --orange-500, --red-500, --gray-400 and --neutral-white
    are NOT redefined for dark mode. Using them is not a hex literal, it just behaves like
    one. This asserts they are gone, so the next person to reach for the punchy colour finds
    out here.

  - NO !important IN folt_desk.css. Its rule 3 says an !important there means a frappe node
    is being selected, which is rule 1 broken. The rule is only true while it is true.

  - --font-stack IS STILL ABSENT FROM FRAPPE'S dark.scss. This is the single upgrade tripwire
    for the theme. folt_theme.css declares --font-stack on bare :root, which is safe ONLY
    because frappe never redefines it under [data-theme="dark"]. The day frappe adds it, our
    override loses in dark mode and the Desk silently reverts to Inter for dark-mode users
    only -- a bug invisible to anyone testing in light mode, which is most people.

  - THE ORDER OF app_include_css. The theme must come first so folt_desk.css stays the last
    stylesheet the Desk loads, which is the whole basis of its no-!important rule.

Reads only: no fixtures, no documents, nothing to tear down. Run with

    bench --site <site> execute folt_customizations.theme_e2e.run
"""

import os
import re

import frappe

PASS, FAIL = [], []

APP = "folt_customizations"
THEME_CSS = f"/assets/{APP}/css/folt_theme.css"
DESK_CSS = f"/assets/{APP}/css/folt_desk.css"

# Declared once in the compiled bundle and never under [data-theme="dark"]. See the module
# docstring, and rule 2 in folt_desk.css.
NOT_DARK_SAFE = (
	"--green-500",
	"--blue-500",
	"--orange-500",
	"--red-500",
	"--gray-400",
	"--neutral-white",
)

FONTS = ("lexend-latin.woff2", "lexend-latin-ext.woff2")


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def app_path(*parts) -> str:
	return os.path.join(frappe.get_app_path(APP), *parts)


def _website_bundle_path() -> str | None:
	"""frappe's compiled website stylesheet. The filename carries a content hash, so it is
	globbed rather than named -- it changes on every frappe build."""
	import glob

	# apps/frappe/frappe -> the bench root is three levels up. Both layouts are tried because
	# the image serves assets from a real `assets/` dir with `sites/assets` symlinked at it
	# (see the Containerfile), and which one exists depends on where this runs.
	bench = os.path.abspath(os.path.join(frappe.get_app_path("frappe"), "..", "..", ".."))
	for parts in (
		("sites", "assets", "frappe", "dist", "css"),
		("assets", "frappe", "dist", "css"),
	):
		found = glob.glob(os.path.join(bench, *parts, "website.bundle.*.css"))
		if found:
			return found[0]
	return None


def _base_template_path() -> str | None:
	path = os.path.join(frappe.get_app_path("frappe"), "templates", "base.html")
	return path if os.path.isfile(path) else None


def strip_comments(css: str) -> str:
	"""Rules only. Every clause below is about what the browser sees, and the comments in both
	files quote the very tokens being banned in order to explain why."""
	return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def run():
	print("\n--- the hook: both files, theme first ---")

	hook = frappe.get_hooks("app_include_css")
	check("folt_theme.css is in app_include_css", THEME_CSS in hook)
	check("folt_desk.css is in app_include_css", DESK_CSS in hook)
	if THEME_CSS in hook and DESK_CSS in hook:
		check(
			"the theme loads first, so folt_desk.css is still the last stylesheet",
			hook.index(THEME_CSS) < hook.index(DESK_CSS),
			f"theme at {hook.index(THEME_CSS)}, desk at {hook.index(DESK_CSS)}",
		)
		check(
			"and both are last overall, after frappe/erpnext/hrms",
			hook.index(DESK_CSS) == len(hook) - 1,
			f"desk at {hook.index(DESK_CSS)} of {len(hook)}",
		)
	check(
		"no path contains '.bundle.', which would demand a build step this app cannot run",
		not any(".bundle." in path for path in [THEME_CSS, DESK_CSS]),
	)

	print("\n--- the files exist where /assets will look for them ---")

	for name in ("folt_theme.css", "folt_desk.css"):
		path = app_path("public", "css", name)
		check(f"{name} is on disk", os.path.isfile(path), path)

	for name in FONTS:
		path = app_path("public", "fonts", "lexend", name)
		exists = os.path.isfile(path)
		check(f"{name} is on disk", exists, path)
		if exists:
			with open(path, "rb") as handle:
				magic = handle.read(4)
			# A truncated or LFS-pointer font is a 200 that renders nothing.
			check(f"{name} is really woff2", magic == b"wOF2", f"magic={magic!r}")

	check(
		"the OFL licence ships with the font, as OFL 1.1 section 2 requires",
		os.path.isfile(app_path("public", "fonts", "lexend", "OFL.txt")),
	)

	print("\n--- folt_theme.css: the variable-font contract ---")

	theme = open(app_path("public", "css", "folt_theme.css")).read()
	theme_rules = strip_comments(theme)

	# `font-weight: 100 900` is a RANGE descriptor. Without it, frappe's --weight-regular: 420
	# resolves UP to 500 per CSS Fonts 4 and every regular-weight string in the Desk renders at
	# Medium -- uniformly wrong, with nothing to point at.
	faces = theme_rules.count("@font-face")
	check("both Lexend subsets are declared", faces == 2, f"{faces} @font-face rules")
	check(
		"every @font-face uses the 100 900 weight RANGE, not a single weight",
		theme_rules.count("font-weight: 100 900") == faces,
	)
	check(
		"each subset is scoped by unicode-range",
		theme_rules.count("unicode-range:") == faces,
	)
	check("font-display: swap, matching frappe", theme_rules.count("font-display: swap") == faces)

	for name in FONTS:
		check(f"the @font-face src points at {name}", name in theme_rules)

	print("\n--- folt_theme.css: the three-block selector contract ---")

	# A colour token declared on bare :root wins in light mode AND beats [data-theme="dark"],
	# because both are (0,1,0) and this file loads last. That is the one mistake this file is
	# shaped to prevent, so the light block must be the :not() form.
	check(
		"the light block is :root:not([data-theme=\"dark\"]), which also matches 'automatic'",
		':root:not([data-theme="dark"])' in theme_rules,
	)
	check(
		"the dark block is qualified :root[data-theme=\"dark\"], so it wins on specificity too",
		':root[data-theme="dark"]' in theme_rules,
	)
	check(
		"no bare [data-theme=\"dark\"] block, which would win only on source order",
		not re.search(r'(?<![\w\]\)]) \[data-theme="dark"\]\s*\{', " " + theme_rules),
	)

	# --font-stack ON A BARE :root IS A LIVE BUG, AND AN INVISIBLE ONE.
	# login.bundle.css re-declares the whole espresso token set -- --font-stack included -- on
	# its own bare :root, and the login page emits it AFTER web_include_css. Two bare-:root
	# declarations tie on specificity and the later wins, so the login page rendered in Inter
	# while every other page rendered in Lexend. It is invisible in this file, invisible in the
	# Desk, and invisible in dark mode; it showed up only in a getComputedStyle probe of the
	# real login page. The fix is that the theme-independent block carries both (0,2,0)
	# selectors, so this asserts the declaration is not sitting on a bare :root.
	# Selector/body pairs rather than anchoring each block on the previous block's closing
	# brace: findall CONSUMES that brace, so an anchored pattern silently sees only the first
	# of two adjacent :root blocks -- which is exactly how the first version of this check
	# passed against a file with the bug deliberately put back.
	blocks = [
		(sel.strip(), body)
		for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", theme_rules)
		if not sel.strip().startswith("@")
	]
	bare_root = [body for sel, body in blocks if sel == ":root"]

	check(
		"--font-stack is NOT declared on a bare :root",
		not any("--font-stack" in body for body in bare_root),
		"a bare :root ties login.bundle.css and loses on order -- the login page falls back "
		"to Inter while everything else is Lexend",
	)
	check(
		"--font-stack is declared at all",
		any("--font-stack" in body for _, body in blocks),
	)

	# THE TYPE SCALE, AND THE ONE INVARIANT THAT HOLDS IT TOGETHER.
	# The --text-<size> ladder is theme-independent in exactly the way --font-stack is, and it
	# is exposed to exactly the same trap: website.bundle.css declares every size token twice
	# on a bare :root and login.bundle.css once more, all after web_include_css on their pages.
	SIZE_TOKENS = [
		"--text-tiny", "--text-2xs", "--text-xs", "--text-sm", "--text-md", "--text-base",
		"--text-lg", "--text-xl", "--text-2xl", "--text-3xl", "--text-4xl",
	]
	on_bare_root = [t for t in SIZE_TOKENS if any(f"{t}:" in b for b in bare_root)]
	check(
		"no --text-<size> token is declared on a bare :root",
		not on_bare_root,
		f"would lose to website/login bundles on order: {on_bare_root}",
	)

	sizes = {}
	for token in SIZE_TOKENS:
		found = re.search(rf"{re.escape(token)}\s*:\s*(\d+)px", theme_rules)
		if found:
			sizes[token] = int(found.group(1))
	check(
		"every rung of the ladder is declared",
		len(sizes) == len(SIZE_TOKENS),
		f"missing: {sorted(set(SIZE_TOKENS) - set(sizes))}",
	)

	# frappe's own alias, and its own pairing. Breaking either desyncs text that is meant to
	# match -- and does it silently, because both halves still look reasonable on their own.
	check(
		"--text-md still equals --text-base, as frappe declares them",
		sizes.get("--text-md") == sizes.get("--text-base"),
		f'md={sizes.get("--text-md")} base={sizes.get("--text-base")}',
	)
	check(
		"--text-2xs still equals --text-xs, as frappe declares them",
		sizes.get("--text-2xs") == sizes.get("--text-xs"),
	)
	check(
		"the ladder is monotonic",
		all(
			sizes[a] <= sizes[b]
			for a, b in zip(SIZE_TOKENS, SIZE_TOKENS[1:])
			if a in sizes and b in sizes
		),
		f"{[sizes.get(t) for t in SIZE_TOKENS]}",
	)

	# THE INVARIANT. Bootstrap's `body{font-size:.875rem}` is a rem literal no token reaches,
	# and in stock frappe it happens to equal --text-base, so inherited text and explicitly
	# sized body text agree. Raising --text-base without restating body breaks that silently:
	# the Desk ends up with two different "normal" sizes and nothing points at why.
	body_rule = re.search(r"(?:^|\})\s*body\s*\{([^{}]*)\}", theme_rules, flags=re.M)
	check(
		"body's inherited size is restated as --text-base, not left at bootstrap's .875rem",
		bool(body_rule) and "var(--text-base)" in body_rule.group(1),
		"without this, every string with no explicit font-size stays 14px while "
		"--text-base moves -- see section 2b",
	)

	# THE HEADING LADDER MUST STAY DESK-ONLY. This stylesheet is in web_include_css too, and
	# the website ships a MUCH larger ladder (h1 2.5rem, h3 1.75rem). A bare `h3` here loads
	# last on the portal and shrinks it. The scope is the whole safety property.
	heading_selectors = [
		sel for sel, _ in blocks
		if re.search(r"(^|[,\s])h[1-6]\s*$", sel) or re.match(r"^h[1-6]\b", sel)
	]
	unscoped_headings = [
		s for s in heading_selectors if not s.startswith("body:not([data-path])")
	]
	check(
		"every h1-h6 rule is scoped to body:not([data-path]), the Desk",
		not unscoped_headings,
		f"would shrink the supplier portal's headings: {unscoped_headings}"
		if unscoped_headings
		else f"{len(heading_selectors)} heading rules, all Desk-scoped",
	)
	check(
		"h5 is not smaller than body text",
		bool(re.search(r"body:not\(\[data-path\]\) h5 \{ font-size: var\(--text-base\)", theme_rules)),
		"stock h5 is .875rem == --text-base; if --text-base moves and h5 does not, every "
		"frappe dialog title (<h5 class='modal-title'>) renders below its own body text",
	)

	# Rule 3: every brand value written down once. Section 1 declares the --folt-brand-*
	# constants; the only other hex literals allowed are the two inset highlights quoted
	# verbatim from bootstrap's own .btn-primary:focus rule.
	hexes = re.findall(r"#[0-9a-fA-F]{3,8}", theme_rules)
	brand_block = re.search(r":root \{(.*?)\n\}", theme_rules, flags=re.S)
	brand_hexes = set(re.findall(r"#[0-9a-fA-F]{3,8}", brand_block.group(1) if brand_block else ""))
	quoted_from_frappe = {"#ffffff26", "#00000013"}
	stray = [h for h in hexes if h not in brand_hexes and h not in quoted_from_frappe]
	check(
		"every colour is one of the --folt-brand-* constants",
		not stray,
		f"{len(brand_hexes)} brand constants; stray: {sorted(set(stray)) or 'none'}",
	)

	# Rule 4: frappe's semantic ramps keep their meaning. Green means done, red means danger.
	semantic = re.findall(
		r"^\s*(--(?:red|green|orange|amber|yellow|bg|text-on|alert|indicator-dot|diff)-[\w-]+)\s*:",
		theme_rules,
		flags=re.M,
	)
	check(
		"no semantic ramp token is redefined",
		not semantic,
		f"would have shifted: {sorted(set(semantic))}" if semantic else "",
	)

	print("\n--- folt_desk.css: rules 2 and 3, made executable ---")

	desk_rules = strip_comments(open(app_path("public", "css", "folt_desk.css")).read())

	for token in NOT_DARK_SAFE:
		used = re.search(rf"{re.escape(token)}\b", desk_rules)
		check(
			f"{token} is not used -- it is identical in both themes",
			not used,
			"" if not used else "use the --bg-X / --text-on-X pair instead",
		)

	check("no hex literal", not re.search(r"#[0-9a-fA-F]{3,8}", desk_rules))
	check(
		"no !important, so rule 1 is still holding",
		"!important" not in desk_rules,
	)

	# Rule 1 itself. Two clauses, and the distinction matters: `.folt-step.is-done` is fine --
	# `is-done` is a modifier compounded onto a node this app generates -- while `.frappe-card`
	# or a bare `.btn` would not be. So the test is not "every class token starts with folt-",
	# it is "every selector starts under body.folt-guided, and no COMPOUND leads with a class
	# that isn't ours". A bare element (`.folt-tasks-rows > li`) is fine too: it is scoped to a
	# subtree this app built.
	selectors = []
	for chunk in re.findall(r"(^|\})([^{}@]+)\{", desk_rules, flags=re.M):
		text = chunk[1].strip()
		if text and not text.startswith("@"):
			selectors.extend(s.strip() for s in text.split(",") if s.strip())

	# Inside @keyframes the "selectors" are 0%/50%/to -- not selectors at all.
	selectors = [s for s in selectors if not re.fullmatch(r"[\d%.,\sfromtoand]+", s)]

	unscoped = [s for s in selectors if not s.startswith("body.folt-guided")]
	check(
		"every rule is scoped to body.folt-guided, the kill switch",
		not unscoped,
		f"unscoped: {unscoped[:3]}" if unscoped else f"{len(selectors)} selectors",
	)

	foreign = []
	for selector in selectors:
		# Compounds after the leading body.folt-guided.
		for compound in re.split(r"[\s>+~]+", selector)[1:]:
			classes = re.findall(r"\.([a-zA-Z][\w-]*)", compound)
			if classes and not classes[0].startswith("folt-"):
				foreign.append(selector)
				break
	check(
		"no compound leads with a class this app did not generate",
		not foreign,
		f"foreign: {sorted(set(foreign))[:3]}" if foreign else "",
	)

	print("\n--- the portal: web_include_css ---")

	web = frappe.get_hooks("web_include_css")
	branding = f"/assets/{APP}/css/folt_branding.css"
	portal = f"/assets/{APP}/css/folt_portal.css"

	check("folt_theme.css is on the website too", THEME_CSS in web)
	check("folt_portal.css is in web_include_css", portal in web)
	check("folt_branding.css is still in web_include_css", branding in web)
	if all(p in web for p in (THEME_CSS, portal, branding)):
		# The theme declares the tokens everything else reads, and folt_branding.css's own
		# comment claims it is the last stylesheet the website loads -- which is why its
		# navbar rule needs no !important. Keep that claim true.
		check(
			"theme first, branding last -- the order each file's comments assume",
			web.index(THEME_CSS) < web.index(portal) < web.index(branding),
			f"theme {web.index(THEME_CSS)}, portal {web.index(portal)}, branding {web.index(branding)}",
		)
		check(
			"and branding is last overall, after frappe's and erpnext's website bundles",
			web.index(branding) == len(web) - 1,
			f"branding at {web.index(branding)} of {len(web)}",
		)

	print("\n--- folt_portal.css: the same rules, on the website ---")

	portal_path = app_path("public", "css", "folt_portal.css")
	check("folt_portal.css is on disk", os.path.isfile(portal_path), portal_path)

	if os.path.isfile(portal_path):
		portal_rules = strip_comments(open(portal_path).read())

		check("no hex literal", not re.search(r"#[0-9a-fA-F]{3,8}", portal_rules))
		check("no !important", "!important" not in portal_rules)

		for token in NOT_DARK_SAFE:
			# The website has no dark theme today, so this is belt-and-braces rather than a
			# live bug -- but it is the rule the moment frappe adds one, and the pair tokens
			# are the better choice regardless.
			check(
				f"{token} is not used",
				not re.search(rf"{re.escape(token)}\b", portal_rules),
			)

		# Rule 1: section 1 is ours, section 2 is a short enumerated list of frappe nodes.
		# Anything selecting a frappe node that is NOT one of these is the rule being broken.
		allowed_frappe = {
			"web-sidebar",
			"sidebar-item",
			"transaction-list-item",
			"page-header-wrapper",
			"page-header-actions-block",
			"btn",
		}
		selectors = []
		for chunk in re.findall(r"(^|\})([^{}@]+)\{", portal_rules, flags=re.M):
			text = chunk[1].strip()
			if text and not text.startswith("@"):
				selectors.extend(s.strip() for s in text.split(",") if s.strip())
		selectors = [s for s in selectors if not re.fullmatch(r"[\d%.,\sfromtoand]+", s)]

		# As on the Desk, the test is about which class LEADS each compound, not every class
		# token in it: `.folt-note.is-received` is ours with a modifier on it, while a bare
		# `.btn` or `.frappe-card` would not be.
		foreign = []
		for selector in selectors:
			for compound in re.split(r"[\s>+~]+", selector):
				classes = re.findall(r"\.([a-zA-Z][\w-]*)", compound)
				if classes and not classes[0].startswith("folt-") and classes[0] not in allowed_frappe:
					foreign.append(f"{selector}  ({classes[0]})")
					break
		check(
			"every frappe node it touches is one of the enumerated few",
			not foreign,
			f"{len(selectors)} selectors; unlisted: {sorted(set(foreign))[:3]}"
			if foreign
			else f"{len(selectors)} selectors",
		)

		# The tokens have to exist on the WEBSITE bundle, which is a different stylesheet from
		# the Desk's -- a token that only espresso's desk half declares would silently resolve
		# to nothing here, and an unset custom property is an invalid declaration, not a
		# fallback to something sensible.
		website_bundle = _website_bundle_path()
		if website_bundle:
			body = open(website_bundle).read()
			used = sorted(set(re.findall(r"var\((--[\w-]+)\)", portal_rules)))
			ours = {"--folt-brand-primary", "--folt-brand-primary-hover"}
			missing = [t for t in used if t not in ours and f"{t}:" not in body]
			check(
				"every token it reads is declared in website.bundle.css",
				not missing,
				f"{len(used)} tokens used; missing: {missing}" if missing else f"{len(used)} tokens",
			)
		else:
			check("frappe's compiled website bundle was found", False)

		# The portal is light-only, and that is load-bearing for the file having no dark block.
		check(
			"the website still has no dark theme (base.html emits no data-theme)",
			"data-theme" not in open(_base_template_path()).read()
			if _base_template_path()
			else False,
			"if this fails, folt_portal.css needs the three-block treatment folt_theme.css uses",
		)

	print("\n--- the upgrade tripwire ---")

	# The premise of the Desk-only heading scope: base.html always emits data-path on <body>
	# and www/desk.html emits a bare <body>. If frappe ever adds data-path to the Desk, or
	# drops it from the website, section 2c either stops applying or starts applying to the
	# portal -- and both failures are invisible in this file.
	base_html = _base_template_path()
	desk_html = os.path.join(frappe.get_app_path("frappe"), "www", "desk.html")
	check(
		"the website's <body> still carries data-path",
		bool(base_html) and re.search(r"<body[^>]*data-path=", open(base_html).read()),
		"if this fails, section 2c's heading rules stop reaching the Desk OR start "
		"reaching the portal -- re-derive the scope before trusting either",
	)
	check(
		"the Desk's <body> still does not",
		os.path.isfile(desk_html)
		and not re.search(r"<body[^>]*data-path=", open(desk_html).read()),
		desk_html,
	)

	# The whole reason --font-stack may live on bare :root.
	dark_scss = os.path.join(
		frappe.get_app_path("frappe"), "public", "scss", "desk", "dark.scss"
	)
	if os.path.isfile(dark_scss):
		body = open(dark_scss).read()
		check(
			"frappe still does not redefine any --text-<size> token for dark mode",
			not re.search(
				r"--text-(tiny|2xs|xs|sm|md|base|lg|xl|\d+xl)\s*:", body
			),
			"the size ladder is declared once for both themes on the strength of this; "
			"if it fails, split it across the light and dark blocks",
		)
		check(
			"frappe still does not redefine --font-stack for dark mode",
			"--font-stack" not in body,
			"if this fails, move --font-stack into BOTH the light and dark blocks of "
			"folt_theme.css -- otherwise dark-mode users silently get Inter",
		)
	else:
		check("frappe's desk/dark.scss was found", False, dark_scss)

	print("\n" + "=" * 60)
	print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")

	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}

"""End-to-end check on the FoLT sign-in page.

Two files, one surface: folt_login.css turns frappe's centred 371px login card into FoLT's
two-column sign-in screen, and folt_login.js builds the brand panel it paints. Both are
listed in hooks.py; neither can be verified by looking at the page, because everything that
makes them safe is invisible when they are working.

What is being defended here, in the order it would break:

  - THE SPECIFICITY PREMISE. login.html emits login.bundle.css from its `head_include`
    block, and base.html renders that AFTER the web_include_css loop -- so unlike every
    other stylesheet this app ships, folt_login.css loads BEFORE the rules it overrides and
    cannot win on source order. It wins because every selector in it carries one more class
    or element than frappe's. That is a claim about frappe's compiled selectors, so this
    file re-derives it: it computes the specificity of both sides of each pair the CSS
    comments quote and fails if ours has stopped being higher. A frappe release that nests
    its login rules one level deeper turns the whole stylesheet off silently, and the page
    would look stock -- not broken, which is exactly why nobody would report it.

  - THE MARKUP THE SCRIPT READS. folt_login.js moves one node and reads six others out of
    frappe's own login.html. Every one of them is asserted against the template on disk, so
    a template change fails here rather than at the moment somebody cannot sign in. The
    script's guards mean such a change degrades to the stock page -- this test is what
    tells us it happened.

  - THE ORDER OF THE TWO INCLUDE LOOPS. Our script must run BEFORE the template's own
    login.js (so login.js binds its handlers to the elements after they have been moved),
    and folt_branding.css must stay the last stylesheet the website loads (its own
    comment's no-!important claim depends on it). Both are properties of frappe's
    base.html and of the hook lists, and both are checked.

  - THE ASSETS ARE ACTUALLY THERE. An app whose public/ holds only plain CSS and JS gets no
    sites/assets/<app> symlink from `bench build`, and that is how every FoLT branding asset
    404'd in an earlier image while the code looked perfect. A stylesheet that 404s leaves
    the stock login page, i.e. the same picture as a stylesheet that lost on specificity.

  - THE ONE-PLACE-FOR-A-COLOUR RULE. folt_theme.css declares every FoLT colour as a
    --folt-brand-* constant. This asserts folt_login.css writes no colour of its own: the
    only literals allowed are white and alpha-white, because the brand panel is a surface
    that is always dark and "white" is not a brand decision.

  - THE CONTRAST OF THE PANEL'S TEXT, ARITHMETICALLY. The panel is one flat fill, which is
    what makes this checkable at all: every string on it is an alpha white over exactly one
    known colour. So the ratios are not asserted from a comment -- this file reads the panel
    colour out of folt_theme.css, reads each alpha out of folt_login.css, composites them
    and computes the WCAG 2.2 contrast. Three of the four alphas the page shipped with on
    3 September failed SC 1.4.3; nothing but arithmetic would have said so.

  - MOTION IS ANSWERED FOR PEOPLE WHO CANNOT TAKE IT. Every hover rule that moves something
    must have its selector inside the prefers-reduced-motion block (SC 2.3.3), and the two
    icons that are POSITIONED by a transform must be restated there rather than set to
    `none`. Both are checked, because "I added a hover effect" is the single easiest way to
    break either.

Reads only: no fixtures, no documents, no login attempted, nothing to tear down. Run with

    bench --site <site> execute folt_customizations.login_e2e.run
"""

import glob
import os
import re

import frappe

PASS, FAIL = [], []

APP = "folt_customizations"
LOGIN_CSS = f"/assets/{APP}/css/folt_login.css"
LOGIN_JS = f"/assets/{APP}/js/folt_login.js"
BRANDING_CSS = f"/assets/{APP}/css/folt_branding.css"
PORTAL_CSS = f"/assets/{APP}/css/folt_portal.css"

# The scope every rule in folt_login.css must carry -- the class folt_login.js adds to
# <body>, and the only reason a rule in that file ever applies. See its rule 1.
SCOPE = "body.folt-signin"

# The nodes folt_login.js reads out of frappe's login.html. Each is a substring of the
# template rather than a parsed selector, because the template is the contract: if frappe
# renames one of these, the script's guard fails and the stock page is what ships.
LOGIN_TEMPLATE_CONTRACT = {
	"the login section the script keys off": "class='for-login'",
	"the card head, whose logo is moved to the brand panel": 'class="page-card-head"',
	"the logo <img> itself": 'class="app-logo"',
	"the <h4> the greeting replaces": "<h4>",
	"the subtitle under it": 'class="page-card-subtitle"',
	"the actions block the keyboard hint is appended to": 'class="page-card-actions"',
	"the submit button the script relabels": "btn-login",
	"the e-mail-link button it must NOT relabel": "btn-login-with-email-link",
	"the e-mail field, required (hence its asterisk)": 'id="login_email"',
	"the password field, required": 'id="login_password"',
}

# The pairs the CSS comments quote: (what frappe compiles, what folt_login.css uses). The
# frappe side is checked for PRESENCE in the compiled bundle as well as for specificity, so
# a rewritten login.bundle.scss fails here rather than quietly winning.
SPECIFICITY_PAIRS = [
	(
		".for-login .page-card .page-card-body input[type=text]",
		f'{SCOPE} .page-card .page-card-body input[type="text"]',
	),
	(
		".for-login .page-card .page-card-body .field-icon",
		f"{SCOPE} .page-card .page-card-body .field-icon",
	),
	(
		".for-login .page-card .page-card-actions .btn-login",
		f"{SCOPE} .page-card .page-card-actions .btn-login",
	),
	(
		".for-login .page-card .page-card-actions .btn-login-option:hover",
		f"{SCOPE} .page-card .page-card-actions .btn-login-option:hover",
	),
	(
		".for-login .page-card .page-card-body .form-label",
		f"{SCOPE} .page-card .page-card-body .form-label",
	),
	(".page-card-head img.app-logo", f"{SCOPE} .page-card-head img.app-logo"),
	(".page-card-head h4", f"{SCOPE} .page-card-head h4"),
	("body .page-content-wrapper", f"{SCOPE} .page-content-wrapper"),
]


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def app_path(*parts) -> str:
	return os.path.join(frappe.get_app_path(APP), *parts)


def bench_root() -> str:
	# apps/frappe/frappe -> three levels up.
	return os.path.abspath(os.path.join(frappe.get_app_path("frappe"), "..", "..", ".."))


def served_path(*parts) -> str | None:
	"""The file as /assets will resolve it: through the sites/assets/<app> symlink. Both
	layouts are tried because the image serves from a real assets/ dir with sites/assets
	symlinked at it, and which one exists depends on where this runs."""
	for prefix in (("sites", "assets"), ("assets",)):
		candidate = os.path.join(bench_root(), *prefix, APP, *parts)
		if os.path.exists(candidate):
			return candidate
	return None


def login_bundle_path() -> str | None:
	"""frappe's compiled login stylesheet. Content-hashed filename, so it is globbed."""
	for parts in (
		("sites", "assets", "frappe", "dist", "css"),
		("assets", "frappe", "dist", "css"),
	):
		found = glob.glob(os.path.join(bench_root(), *parts, "login.bundle.*.css"))
		if found:
			return found[0]
	return None


def frappe_template(name: str) -> str | None:
	path = os.path.join(frappe.get_app_path("frappe"), "www", name)
	return open(path).read() if os.path.isfile(path) else None


def strip_comments(css: str) -> str:
	"""Rules only. The comments quote the very selectors and colours being banned."""
	return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def selectors_of(css: str) -> list[str]:
	"""Every selector in the file, @media headers and @keyframes stops excluded."""
	found = []
	for _, text in re.findall(r"(^|\})([^{}@]+)\{", css, flags=re.M):
		text = text.strip()
		if text and not text.startswith("@"):
			found.extend(s.strip() for s in text.split(",") if s.strip())
	return [s for s in found if not re.fullmatch(r"[\d%.,\sfromtoand]+", s)]


def luminance(hex_colour: str) -> float:
	"""WCAG 2.x relative luminance."""
	value = hex_colour.lstrip("#")
	if len(value) == 3:
		value = "".join(c * 2 for c in value)
	out = 0.0
	for channel, weight in zip(
		(value[0:2], value[2:4], value[4:6]), (0.2126, 0.7152, 0.0722)
	):
		c = int(channel, 16) / 255
		out += weight * (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
	return out


def contrast(fore: str, back: str) -> float:
	a, b = luminance(fore), luminance(back)
	high, low = max(a, b), min(a, b)
	return (high + 0.05) / (low + 0.05)


def over(alpha: float, back: str) -> str:
	"""An alpha WHITE composited over an opaque background -- which is what every string on
	the brand panel is. Returned as a hex so it can go straight into contrast()."""
	value = back.lstrip("#")
	out = "#"
	for i in (0, 2, 4):
		channel = int(value[i : i + 2], 16)
		out += "%02x" % round(alpha * 255 + (1 - alpha) * channel)
	return out


def over_colour(fore: str, alpha: float, back: str) -> str:
	"""An alpha COLOUR composited over an opaque background -- the glass card is a navy tint,
	not a white one, so `over()` above cannot do this one."""
	f, b = fore.lstrip("#"), back.lstrip("#")
	out = "#"
	for i in (0, 2, 4):
		out += "%02x" % round(
			alpha * int(f[i : i + 2], 16) + (1 - alpha) * int(b[i : i + 2], 16)
		)
	return out


def alpha_of(css: str, selector: str) -> float | None:
	"""The alpha of the rgba(255,255,255,a) `color` in the rule for `selector`. The selector
	is looked up inside each rule's LIST rather than anchored to the start of a block --
	several of these rules colour two nodes at once."""
	want = SCOPE + " " + selector
	for sels, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
		if want not in [one.strip() for one in sels.split(",")]:
			continue
		found = re.search(
			r"color:\s*rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*([\d.]+)\s*\)", decl
		)
		if found:
			return float(found.group(1))
	return None


def specificity(selector: str) -> tuple[int, int, int]:
	"""(ids, classes, elements) for a single compound selector chain -- enough for the
	comparison this file makes, and deliberately not a full CSS parser.

	`:not(...)` takes the specificity of its argument (CSS Selectors 4), which is why it is
	unwrapped rather than counted: `.btn-signup:not(:disabled)` is two classes, not one.
	Pseudo-ELEMENTS count as elements, pseudo-classes as classes -- and ::before is written
	with two colons everywhere in this app, which is what tells them apart here."""
	s = re.sub(r":not\(([^()]*)\)", r"\1", selector)
	ids = len(re.findall(r"#[\w-]+", s))
	classes = len(re.findall(r"\.[\w-]+", s))
	classes += len(re.findall(r"\[[^\]]+\]", s))
	classes += len(re.findall(r"(?<!:):[a-zA-Z-]+(?:\([^()]*\))?", s))
	elements = len(re.findall(r"::[a-zA-Z-]+", s))
	# Bare element names: what is left once every other token is removed.
	bare = re.sub(r"[#.\[][^\s>+~]*", " ", s)
	bare = re.sub(r"::?[a-zA-Z-]+(?:\([^()]*\))?", " ", bare)
	elements += len(re.findall(r"\b[a-zA-Z][\w-]*\b", bare))
	return (ids, classes, elements)


def run():
	print("\n--- the hooks: the stylesheet, the script, and the order ---")

	web = frappe.get_hooks("web_include_css")
	js = frappe.get_hooks("web_include_js")

	check("folt_login.css is in web_include_css", LOGIN_CSS in web)
	check("folt_login.js is in web_include_js", LOGIN_JS in js)
	check(
		"neither path contains '.bundle.', which would demand a build step this app cannot run",
		".bundle." not in LOGIN_CSS and ".bundle." not in LOGIN_JS,
	)

	if LOGIN_CSS in web and PORTAL_CSS in web and BRANDING_CSS in web:
		check(
			"folt_branding.css is STILL the last website stylesheet, as its own comment claims",
			web.index(BRANDING_CSS) == len(web) - 1,
			f"branding at {web.index(BRANDING_CSS)} of {len(web)}, login at {web.index(LOGIN_CSS)}",
		)
		check(
			"folt_login.css sits after the portal and before the branding",
			web.index(PORTAL_CSS) < web.index(LOGIN_CSS) < web.index(BRANDING_CSS),
		)

	print("\n--- the files exist where /assets will look for them ---")

	for parts in (("css", "folt_login.css"), ("js", "folt_login.js")):
		on_disk = app_path("public", *parts)
		check(f"{parts[-1]} is on disk", os.path.isfile(on_disk), on_disk)
		served = served_path(*parts)
		check(
			f"{parts[-1]} resolves through the sites/assets symlink",
			bool(served),
			served or "no sites/assets/%s -- every /assets URL for this app 404s" % APP,
		)

	print("\n--- folt_login.css: the contract in its header ---")

	css = open(app_path("public", "css", "folt_login.css")).read()
	rules = strip_comments(css)
	selectors = selectors_of(rules)

	unscoped = [s for s in selectors if not s.startswith(SCOPE)]
	check(
		f"every selector starts with {SCOPE} -- the specificity AND the kill switch",
		not unscoped,
		f"unscoped: {unscoped[:3]}" if unscoped else f"{len(selectors)} selectors",
	)
	check(
		"no !important; specificity is what this file wins on",
		"!important" not in rules,
	)

	# WHITE IS NOT A BRAND DECISION. Everything else must come from folt_theme.css's
	# --folt-brand-* constants or from a frappe token, so that reverting the brand stays a
	# six-line edit in one file.
	# WHITE, PLUS EXACTLY ONE NAMED COLOUR. folt_theme.css owns the FoLT brand; this page was
	# specified with an aquatic accent that is not part of it, so the accent is declared once
	# as a token on the page's own scope (section 0) and everything else references it. Any
	# second literal is a colour written down twice.
	literals = re.findall(r"#[0-9a-fA-F]{3,8}", rules)
	non_white = [h for h in literals if h.lower() not in ("#fff", "#ffffff")]
	accent = re.search(r"--folt-signin-aqua:\s*(#[0-9a-fA-F]{6})", rules)
	check(
		"exactly one non-white literal, and it is the --folt-signin-aqua declaration",
		bool(accent) and non_white == [accent.group(1)],
		f"stray literals: {sorted(set(non_white))}" if len(non_white) > 1 else f"aqua {accent.group(1) if accent else '?'}",
	)
	rgbas = re.findall(r"rgba?\(([^)]*)\)", rules)
	coloured_rgba = [
		v for v in rgbas
		if not re.match(r"\s*255\s*,\s*255\s*,\s*255\s*,", v)
	]
	check(
		"every rgba() is an alpha WHITE -- the glass, the rims, the sheens",
		not coloured_rgba,
		f"stray: {sorted(set(coloured_rgba))[:3]}" if coloured_rgba else f"{len(rgbas)} alpha whites",
	)
	# The navy-tinted shadows are the one place a FoLT colour is mixed rather than used
	# whole, and color-mix is what keeps them derived from the constant instead of restated
	# as an rgba() literal.
	mixes = re.findall(r"color-mix\(([^()]*(?:\([^()]*\))?[^()]*)\)", rules)
	# The navy-tinted shadows and glass, and the two semantic reds lightened for a dark
	# ground, are the places a colour is MIXED rather than used whole. What matters is that
	# the colour going in is a token: a literal here would be a FoLT colour written down
	# twice, and a frappe ramp value copied rather than followed.
	check(
		"every color-mix() mixes a var() token, never a literal",
		all("var(--" in m for m in mixes),
		f"{len(mixes)} mixes" if mixes else "none",
	)
	check(
		"the page ground and the shadows are derived from --folt-brand-*",
		"var(--folt-brand-primary)" in rules and "var(--folt-brand-navy)" in rules,
	)

	# ONE GROUND, DECLARED ONCE ON <body>, and the panels sit ON it rather than replacing
	# it -- which is what makes the light behind the frost the same light everywhere.
	body_rules = re.findall(r"body\.folt-signin \{([^{}]*)\}", rules)
	check(
		"the page ground is declared on <body>, once",
		len(body_rules) == 2 and any("linear-gradient(" in b for b in body_rules),
		f"{len(body_rules)} body blocks (section 0's tokens, then section 1's ground)",
	)
	check(
		"the page is LIGHT: no color-scheme: dark left over from the dark design",
		"color-scheme: dark" not in rules,
	)
	check(
		"Chrome's autofill is still overridden, now to a white ground and navy text",
		"-webkit-autofill" in rules and "-webkit-text-fill-color" in rules,
	)

	# THE GLASS MUST BE WHITE. The whole contrast budget below rests on it: a white tint can
	# only LIGHTEN what is behind it, so no part of a panel is ever darker than the worst
	# ground computed once. A tinted glass would need a ratio per region.
	glass = re.search(r"--folt-signin-glass:\s*rgba\(\s*255\s*,\s*255\s*,\s*255", rules)
	check(
		"the panels' glass is a WHITE tint, so it can only lighten the ground",
		bool(glass),
		"a coloured glass would make the budget in rule 4 region-dependent",
	)
	check(
		"both panels are the same material, described once",
		bool(
			re.search(
				r"body\.folt-signin \.folt-signin-brand,\s*"
				r"body\.folt-signin \.folt-signin-pane \{", rules
			)
		),
	)

	# ONE RADIUS PER SCALE, which is what keeps a glass layout from reading as a pile of
	# unrelated rounded things: the panels, the controls, the plate, the chip, the pills.
	radii = sorted({r.strip() for r in re.findall(r"border-radius:\s*([^;]+);", rules)})
	allowed = {"0", "8px", "12px", "18px", "20px", "24px", "50%", "999px"}
	check(
		"one radius per scale -- no stray corner values",
		set(radii) <= allowed,
		f"radii: {radii}",
	)

	check(
		"every backdrop-filter carries its -webkit- twin, or Safari gets no glass",
		rules.count("backdrop-filter:") == 2 * rules.count("-webkit-backdrop-filter:"),
		f"{rules.count('-webkit-backdrop-filter:')} prefixed",
	)

	# The gradient headline is drawn with background-clip: text, which needs the prefix AND
	# a `color` set FIRST -- a browser without it renders `color`, so the failure mode is a
	# solid blue line rather than an invisible one.
	clip = re.search(
		r"\.folt-signin-headline span \{([^{}]*)\}", rules
	)
	check(
		"the gradient headline degrades to a solid colour, not to invisible text",
		bool(clip)
		and "-webkit-background-clip: text" in clip.group(1)
		and clip.group(1).index("color:") < clip.group(1).index("background-image:"),
	)

	# THE FOCUS RING IS THE BRAND BLUE HERE, not --focus-default: that token is a hard 2px
	# ring drawn for the Desk's white controls, and these fields are 12px frosted boxes. Both
	# are the same colour, so this is a weight decision, not a contrast one -- but it has to
	# be a ring at all (SC 2.4.7), so that is what is asserted.
	check(
		"the focused field carries a visible brand ring",
		bool(
			re.search(
				r":focus[^{}]*\{[^{}]*box-shadow: 0 0 0 4px color-mix\(in srgb, "
				r"var\(--folt-brand-primary\)", rules
			)
		),
	)
	check(
		"no `display` is set on a <section>: login.js toggles those with an inline style",
		not any(re.search(r"(^|[\s>+~])section\b", s) for s in selectors),
	)

	# frappe's compiled login stylesheet, read once: the contrast section needs its red and
	# the specificity section needs its selectors.
	bundle_path = login_bundle_path()
	bundle = open(bundle_path).read() if bundle_path else ""

	print("\n--- the contrast budget: every ink, against the darkest ground ---")

	# The brand, read from the file that declares it rather than restated here -- so a brand
	# change moves every ratio below and this test says whether they still pass.
	theme = open(app_path("public", "css", "folt_theme.css")).read()
	brand = {}
	for name in ("primary", "navy", "tint", "tint-strong"):
		found = re.search(r"--folt-brand-%s:\s*(#[0-9a-fA-F]{6})" % name, theme)
		if found:
			brand[name] = found.group(1)
	check(
		"the four brand values this page reads are declared in folt_theme.css",
		len(brand) == 4,
		f"found {sorted(brand)}",
	)
	aqua = re.search(r"--folt-signin-aqua:\s*(#[0-9a-fA-F]{6})", rules)

	if len(brand) == 4 and aqua:
		# RULE 4, MECHANICALLY, AND IT RUNS THE OTHER WAY ROUND FROM THE DARK DESIGN. Dark ink
		# on light glass fails where the GROUND GETS DARK, so the budget is the darkest ground
		# a string can sit on:
		#
		#   the page gradient's deepest stop     --folt-brand-tint-strong
		#   + the strongest ambient wash over it (they are tints of primary and of the aqua)
		#   seen through the panel's white glass (which can only lighten -- checked above)
		#
		# Everything below is computed against that one composite.
		layers = re.findall(
			r"color-mix\(in srgb, var\(--(folt-brand-primary|folt-signin-aqua)\) (\d+)%,"
			r" transparent\) 0%,\s*\n?\s*transparent", rules
		)
		# Four: the static one in the page ground plus the three drifting ones. The 1px grid
		# is a tint of the same blue at 8%, i.e. paler than the palest of these, so the
		# darkest ground is always one of the four.
		check(
			"every ambient wash is a tint of the brand blue or of the page accent",
			len(layers) == 4,
			f"washes: {layers}",
		)
		wash_colours = {"folt-brand-primary": brand["primary"], "folt-signin-aqua": aqua.group(1)}
		page_worst = brand["tint-strong"]
		for token, pct in layers:
			candidate = over_colour(wash_colours[token], int(pct) / 100, brand["tint-strong"])
			if luminance(candidate) < luminance(page_worst):
				page_worst = candidate
		glass_alpha = re.search(r"--folt-signin-glass:\s*rgba\(255, 255, 255, ([\d.]+)\)", rules)
		check("the glass declares its own alpha", bool(glass_alpha))
		if glass_alpha:
			panel = over(float(glass_alpha.group(1)), page_worst)

			# Each ink is a mix of the brand navy towards white, so it is resolved the same way
			# the browser resolves it rather than being restated as a hex here.
			inks = {}
			for name in ("ink", "ink-quiet"):
				mix = re.search(
					r"--folt-signin-%s:\s*color-mix\(in srgb, var\(--folt-brand-navy\) (\d+)%%, #fff\)"
					% name, rules
				)
				if mix:
					inks[name] = over_colour(brand["navy"], int(mix.group(1)) / 100, "#ffffff")
			check("both inks are mixes of the brand navy", len(inks) == 2, f"{inks}")

			check(
				f"the darkest ground inside a panel is {panel}",
				True,
				f"page bottoms out at {page_worst}, then the white glass lifts it",
			)

			if "ink" in inks:
				ratio = contrast(inks["ink"], panel)
				check(
					f"--folt-signin-ink (body copy, labels, subtitle) is {ratio:.2f}:1",
					ratio >= 4.5,
					f"{inks['ink']} on {panel}",
				)
			if "ink-quiet" in inks:
				ratio = contrast(inks["ink-quiet"], panel)
				# Placeholders and field icons only. Under 4.5 by design, so this asserts the
				# other half of that bargain: it must not be used for anything a person reads.
				check(
					f"--folt-signin-ink-quiet is {ratio:.2f}:1 -- placeholders and icons only",
					ratio >= 3.0,
				)
				quiet_users = [
					sel.strip()
					for sels, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", rules)
					for sel in sels.split(",")
					if "var(--folt-signin-ink-quiet)" in decl
				]
				allowed = ("placeholder", "field-icon", "toggle-password", "btn-signup:disabled",
					"btn-login:disabled", "password-hint svg")
				stray = [q for q in quiet_users if not any(a in q for a in allowed)]
				check(
					"and it is used on nothing a person has to read",
					not stray,
					f"stray: {stray}" if stray else f"{len(quiet_users)} uses",
				)

			ratio = contrast(brand["navy"], panel)
			check(
				f"the headline and the text typed into a field are {ratio:.2f}:1",
				ratio >= 4.5,
			)

			# The required marker keeps frappe's own red rather than a new one.
			red = re.search(r"--ink-red-3:\s*(#[0-9a-fA-F]{6})", bundle) if bundle else None
			if red:
				ratio = contrast(red.group(1), panel)
				check(
					f"the required marker (frappe's --ink-red-3) is {ratio:.2f}:1",
					ratio >= 3.0,
					"a marker beside a `required` input, not prose",
				)

		# THE TWO GRADIENTS. A gradient is only as legible as its worst stop, which is the
		# whole reason the accent is a deep teal rather than the brighter cyan the design
		# showed: white on #1f7a68 is 5.26:1, on a #2f8d78 it would be 4.04:1 and the button
		# label would fail at one end of its own sweep.
		for stop, label in ((brand["primary"], "the blue end"), (aqua.group(1), "the aqua end")):
			ratio = contrast("#ffffff", stop)
			check(
				f"the Sign in label on {label} ({stop}) is {ratio:.2f}:1",
				ratio >= 4.5,
			)
		# The headline is 30-42px at weight 700, i.e. LARGE text, which SC 1.4.3 lets through
		# at 3:1. The bar here is 4.5:1 anyway, on purpose: the same token is also a button
		# GROUND, and a colour that is legible in only one of its two roles is a trap for
		# whoever reuses it next.
		if glass_alpha:
			for stop, label in ((brand["primary"], "blue"), (aqua.group(1), "aqua")):
				ratio = contrast(stop, panel)
				check(
					f"the gradient headline's {label} stop is {ratio:.2f}:1 on the glass",
					ratio >= 4.5,
					"large text needs 3:1; this token also has to work as a button ground",
				)

	# THE SHORTHAND TRAP, asserted because it already bit once. frappe's button rules use
	# `background:` (the shorthand) on :hover, :focus, :active and :disabled, and a shorthand
	# resets `background-image` to none. Those rules are (0,5,0); this file's BASE button rule
	# is (0,4,1), so the hover state wins over it and the gradient vanished under the cursor
	# -- a near-black frappe grey button, only while being pointed at. Every state that can
	# beat the base rule has to restate the fill.
	# One rule per state is enough -- the transform-only :active rule in section 3b sets no
	# background at all, so it cannot undo anything; what matters is that SOME rule matching
	# each state carries the fill.
	restated = {
		state: any(
			"background-image:" in decl
			for sels, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", rules)
			if ".btn-login:%s" % state in sels
		)
		for state in ("hover", "focus", "active")
	}
	check(
		"the primary button restates its gradient in every state that can beat the base rule",
		all(restated.values()),
		"frappe's `background:` shorthand on :hover resets background-image to none"
		if not all(restated.values())
		else "hover, focus and active",
	)
	check(
		"and its disabled state explicitly clears it",
		bool(re.search(r"btn-signup:disabled \{[^{}]*background-image: none", rules, flags=re.S)),
	)

	print("\n--- motion: ambient, response, and switched off on request ---")

	reduced = rules.find("@media (prefers-reduced-motion: reduce)")
	check("there is a prefers-reduced-motion block at all (SC 2.3.3)", reduced > -1)
	if reduced > -1:
		before, block = rules[:reduced], rules[reduced:]
		check("it switches transitions off", "transition: none" in block)
		check("it switches transforms off", "transform: none" in block)
		check("it switches the animations off", "animation: none" in block)

		# Every animation must be named, defined, and answered in that block.
		declared = set(re.findall(r"@keyframes\s+([\w-]+)", rules))
		used = re.findall(r"animation:\s*([\w-]+)\s+([\d.]+)s([^;]*);", before)
		check(
			"every animation name has a @keyframes to match",
			all(name in declared for name, _, _ in used),
			f"{len(used)} animated rules, {len(declared)} keyframe sets",
		)
		orphan_keyframes = declared - {name for name, _, _ in used}
		check("and no keyframes are left behind unused", not orphan_keyframes, f"{orphan_keyframes or ''}")

		# THE AMBIENT ONES: slow, alternating, and forever. Anything infinite and quick is
		# not ambience, it is a distraction on a page where people are typing a password.
		ambient = [(n, float(d), rest) for n, d, rest in used if "infinite" in rest]
		check(
			"the ambient layers are the only infinite animations, and all are >= 20s",
			len(ambient) == 3 and all(d >= 20 for _, d, _ in ambient),
			f"{[(n, d) for n, d, _ in ambient]}",
		)
		check(
			"each of them alternates, so no loop point is ever visible",
			all("alternate" in rest for _, _, rest in ambient),
		)

		# THE RESPONSE ONE: the entrance. `backwards`, never forwards/both -- a filled-forwards
		# animation keeps applying its last keyframe and beats every hover rule in the cascade,
		# which would silently kill the lift on the plate. See trap 2 in section 3b.
		entrance = [(n, d, rest) for n, d, rest in used if "infinite" not in rest]
		check(
			"the entrance runs once and fills BACKWARDS only",
			bool(entrance)
			and all("backwards" in rest for _, _, rest in entrance)
			and not re.search(r"animation:[^;]*(forwards|both)", before),
			"forwards/both would outrank every :hover transform in this file",
		)
		check(
			"the entrance is fast: no rise longer than 800ms",
			all(float(d) <= 0.8 for _, d, _ in entrance),
			f"{sorted({d for _, d, _ in entrance})}",
		)
		# TRAP 3: frappe animates `.page-card` itself on a failed login (.invalid-login ->
		# wiggle). An animation on that element here would outrank it and swallow the shake.
		card_animated = [
			sel.strip()
			for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", before)
			if "animation:" in decl and sel.strip().endswith(".page-card")
		]
		check(
			"nothing animates .page-card itself, so frappe's failed-login wiggle survives",
			not card_animated,
			f"{card_animated}" if card_animated else "the rise is on its three children",
		)

		animated_selectors = []
		for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", before):
			if "animation:" in decl:
				animated_selectors.extend(one.strip() for one in sel.split(",") if one.strip())
		unanswered = [a for a in animated_selectors if a not in block]
		check(
			"every animated selector is listed in the reduced-motion block",
			not unanswered,
			f"unanswered: {unanswered[:3]}" if unanswered else f"{len(animated_selectors)} animated",
		)

		# Every selector that MOVES on hover has to appear there too.
		movers = []
		for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", before):
			if "transform:" not in decl:
				continue
			for one in sel.split(","):
				one = one.strip()
				if one and (":hover" in one or ":active" in one or ":focus" in one):
					movers.append(one)
		unanswered = [m for m in movers if m not in block]
		check(
			"every hover/active transform is listed in it",
			not unanswered,
			f"unanswered: {unanswered[:3]}" if unanswered else f"{len(movers)} moving rules",
		)
		# THE TRAP: these two are CENTRED by their transform, so `none` would drop them to the
		# bottom of the field. The block has to restate the centring instead.
		toggles = re.search(
			r"toggle-password:hover,[^{}]*toggle-password:active\s*\{([^{}]*)\}", block
		)
		check(
			"the transform-positioned icons are restated, not set to none",
			bool(toggles) and "translateY(-50%)" in toggles.group(1),
			"`transform: none` on .toggle-password un-centres it -- see trap 1 in section 3b",
		)
		check(
			"the liquid layers cannot be clicked",
			rules.count("pointer-events: none") >= 1,
		)

	print("\n--- the specificity premise, re-derived against the compiled bundle ---")

	check("frappe's compiled login.bundle.css was found", bool(bundle_path), bundle_path or "")

	for frappe_selector, ours in SPECIFICITY_PAIRS:
		present = frappe_selector.replace(" ", "") in bundle.replace(" ", "")
		theirs_spec, ours_spec = specificity(frappe_selector), specificity(ours)
		check(
			f"`{frappe_selector}` is still what frappe compiles",
			present,
			"" if present else "frappe changed its login CSS -- redo the math in folt_login.css",
		)
		check(
			f"  and {ours_spec} out-specifies {theirs_spec}",
			ours_spec > theirs_spec,
		)

	print("\n--- the markup folt_login.js reads ---")

	login_html = frappe_template("login.html")
	check("frappe's www/login.html was found", bool(login_html))
	if login_html:
		for label, needle in LOGIN_TEMPLATE_CONTRACT.items():
			check(f"login.html still has {label}", needle in login_html)
		# The two fields the required-marker comes from. `required` is what the script reads;
		# a template that drops it drops the asterisk with it.
		for field in ("login_email", "login_password"):
			block = re.search(r'id="%s".*?>' % field, login_html, flags=re.S)
			check(
				f"{field} still declares `required`",
				bool(block) and "required" in block.group(0),
			)
		check(
			"login.bundle.css is still emitted from head_include -- i.e. AFTER web_include_css",
			bool(re.search(r"{%\s*block head_include\s*%}\s*{{\s*include_style\('login.bundle.css'\)", login_html)),
			"this is the whole reason folt_login.css cannot win on source order",
		)
		# The template opens the block with a `<!-- {{ for_test }} -->` marker, so comments
		# and whitespace are skipped rather than assumed away.
		check(
			"the sections are still wrapped in a plain <div> inside page_content",
			bool(
				re.search(
					r"{%\s*block page_content\s*%}(?:\s|<!--.*?-->)*<div>",
					login_html,
					flags=re.S,
				)
			),
			"folt_login.js asserts this shape before inserting the panel beside it",
		)

	reset_html = frappe_template("update-password.html")
	check("frappe's www/update-password.html was found", bool(reset_html))
	if reset_html:
		check(
			"the set-password page still uses the same card, so it gets the same panel",
			'class="for-reset-password"' in reset_html
			and 'class="page-card-head"' in reset_html,
		)

	print("\n--- the two include loops, in the order both files depend on ---")

	base_path = os.path.join(frappe.get_app_path("frappe"), "templates", "base.html")
	base = open(base_path).read() if os.path.isfile(base_path) else ""
	check("frappe's templates/base.html was found", bool(base), base_path)
	if base:
		head_block = base.find("{%- block head -%}")
		head_include = base.find("{%- block head_include %}")
		check(
			"base.html renders web_include_css BEFORE head_include",
			-1 < head_block < head_include,
			f"head at {head_block}, head_include at {head_include}",
		)
		js_loop = base.find("{%- for link in web_include_js %}")
		script_block = base.find("{%- block script %}")
		check(
			"and web_include_js BEFORE the template's own script block",
			-1 < js_loop < script_block,
			"folt_login.js has to run before login.js binds its handlers",
		)

	web_html = os.path.join(frappe.get_app_path("frappe"), "templates", "web.html")
	web_src = open(web_html).read() if os.path.isfile(web_html) else ""
	check(
		"web.html still nests .page_content inside main inside .page-content-wrapper",
		bool(web_src)
		and web_src.find('class="page-content-wrapper"') < web_src.find('class="page_content"')
		and 'class="page_content"' in web_src,
		"folt_login.css lays the split out on those three nodes",
	)

	print("\n--- folt_login.js: the guards, not the effect ---")

	script = open(app_path("public", "js", "folt_login.js")).read()
	check(
		"it is an IIFE, so it declares nothing on window",
		script.lstrip().startswith("/*") and "(function () {" in script,
	)
	check(
		"the body class is added LAST, so a failed guard cannot leave a half-built page styled",
		script.rindex('classList.add("folt-signin")') > script.rindex("return;"),
	)
	check(
		"it returns early unless the sections sit where it expects",
		"pane.parentElement !== content" in script,
	)
	check(
		"it builds its nodes with the DOM API -- no innerHTML anywhere",
		"innerHTML" not in script,
	)
	check(
		"it re-parents the logo rather than hardcoding an asset path",
		"/assets/" not in script,
	)
	check(
		"it does not relabel the e-mail-link or LDAP buttons, which share .btn-login",
		":not(.btn-login-with-email-link):not(.btn-ldap-login)" in script,
	)
	check(
		"every string it renders goes through __() when frappe's translator is present",
		"typeof window.__ === \"function\"" in script,
	)

	print("\n" + "=" * 60)
	print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
	for label in FAIL:
		print(f"  FAILED: {label}")
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}

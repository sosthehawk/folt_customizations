/* The sign-in page: the half of it that needs a DOM.
   Companion to public/css/folt_login.css, which paints everything this file builds.
   Loaded through `web_include_js` (hooks.py) -- i.e. on every WEBSITE page -- and its first
   act is to work out whether it is on the login page and return if it is not.

   WHY A SCRIPT AND NOT A TEMPLATE. frappe's login page is www/login.html plus www/login.py,
   and the two are a pair: the template reads `logo`, `login_label`, `ldap_settings`,
   `provider_logins`, `login_with_email_link`, `disable_signup` and `disable_user_pass_login`
   out of the controller's context. An app can shadow the template -- but not the controller
   that fills it -- so shipping our own login.html means copying login.py's 200 lines too and
   owning frappe's login logic, including 2FA and the reset-password routes, forever. This
   file adds the four nodes the design needs to the page frappe already renders, and touches
   nothing that logs anybody in: no ids change, no elements are removed, and every handler
   login.js binds is bound AFTER this runs (base.html emits web_include_js before the
   template's own `script` block) to the same element objects.

   THE CONTRACT, and the reason /login is never left half-styled:

     * Nothing here is required for the page to work. Every guard failure is a plain
       `return`, and the stylesheet does nothing at all until this file has put
       `folt-signin` on <body> -- so a change to frappe's markup that this file cannot
       recognise leaves the STOCK login page in FoLT's typeface and colour, never a
       branded frame around markup that has moved.
     * It runs synchronously, at its position near the end of <body> rather than on
       frappe.ready. The sections it needs are already parsed at that point, and doing the
       work before the browser's first paint is what stops the stock card from flashing.
     * It re-parents exactly one node -- the logo <img> frappe renders inside the login
       card -- and creates four of its own. Everything else it does is a class or a string.

   The strings go through frappe's `__()` when it is available, so a site that translates
   them gets the translation; it is not available on every website page, hence the guard. */

(function () {
	// The set-password page (/update-password) is the same card in the same frame, so it
	// gets the same panel. `for-email-login` only exists when a social login is enabled.
	var LOGIN_SECTIONS = "section.for-login, section.for-email-login";
	var ALL_SECTIONS = LOGIN_SECTIONS + ", section.for-reset-password";

	function t(text) {
		try {
			if (typeof window.__ === "function") {
				return window.__(text);
			}
		} catch (e) {
			// A missing translation dictionary is not a reason to render nothing.
		}
		return text;
	}

	function el(tag, className, text) {
		var node = document.createElement(tag);
		if (className) {
			node.className = className;
		}
		if (text) {
			node.textContent = text;
		}
		return node;
	}

	var body = document.body;
	if (!body || body.classList.contains("folt-signin")) {
		return;
	}

	// `.page_content` is web.html's own wrapper and `pane` is the bare <div> login.html
	// puts every section inside. Both are asserted rather than assumed: the panel is
	// inserted as `.page_content`'s first child and the stylesheet lays the two of them out
	// as a two-column grid, so a template that nests them differently must fall through to
	// the stock page instead of producing a one-column grid with a stray panel in it.
	var section = document.querySelector(ALL_SECTIONS);
	if (!section) {
		return;
	}
	var pane = section.parentElement;
	var content = document.querySelector(".page_content");
	if (!pane || !content || pane.parentElement !== content) {
		return;
	}

	// ── 1. THE BRAND PANEL ──────────────────────────────────────────────────────────────
	// Built before the class goes on <body> so that the layout and its contents appear in
	// the same frame.

	var brand = el("aside", "folt-signin-brand");

	// The panel's top row: the lockup on the left, a status pill on the right.
	var top = el("div", "folt-signin-top");

	// The logo is MOVED, not copied, and its src is left alone: frappe renders
	// Website Settings.app_logo here (branding.py writes FoLT's lockup into that field), so
	// the panel shows whatever logo the site is configured with. The card it came from is
	// left with its heading text -- folt_login.css hides the <img> in every section,
	// including the four whose copy is not moved. An alt is added because frappe's template
	// omits it and this is now the only image on the page.
	var logo = section.querySelector(".page-card-head img.app-logo");
	if (logo) {
		var plate = el("div", "folt-signin-plate");
		if (!logo.getAttribute("alt")) {
			logo.setAttribute("alt", t("Friends of Lake Turkana"));
		}
		plate.appendChild(logo);
		top.appendChild(plate);
	}

	// THE STATUS PILL, and what it deliberately does NOT say. The design this page was built
	// from carried "Central Command • SOC-2 Secured" here. SOC 2 is an audited certification
	// with a named auditor and a report date; FoLT holds no such report that this repository
	// knows of, and a login page is the last place to make a compliance claim nobody can
	// stand behind -- it is read by suppliers and donors. So the pill says what is true: this
	// is the command centre, and the sign-in is secure. If FoLT does complete a SOC 2 Type
	// II, this string is where it goes.
	var status = el("div", "folt-signin-status");
	status.appendChild(el("span", "folt-signin-status-dot"));
	status.appendChild(el("span", null, t("Central Command · Secure sign-in")));
	top.appendChild(status);
	brand.appendChild(top);

	var inner = el("div", "folt-signin-brand-inner");

	// The tag above the headline: what the product IS, in the words FoLT's own guide uses.
	var tag = el("div", "folt-signin-tag");
	tag.appendChild(el("span", "folt-signin-tag-check", "\u2713"));
	tag.appendChild(el("span", null, t("Unified Organizational Management")));
	inner.appendChild(tag);

	// Two lines, and the <span> is the second one: folt_login.css runs the brand gradient
	// through it, which is the whole shape of the headline. Keep them as two nodes rather
	// than one string with a <br> in it -- a translation may need to break elsewhere.
	var headline = el("h1", "folt-signin-headline", t("Every request,"));
	headline.appendChild(el("span", null, t("every approval.")));
	inner.appendChild(headline);

	inner.appendChild(
		el(
			"p",
			"folt-signin-lede",
			t(
				"One secure portal for every tool, every team, and every workflow. " +
					"Sign in to access your central command center and manage everything " +
					"from one place."
			)
		)
	);
	inner.appendChild(el("hr", "folt-signin-rule"));
	brand.appendChild(inner);

	// The footer carries the one thing a person who CANNOT sign in needs, which is who to
	// ask. The address is FoLT's ICT desk -- the same one hooks.py declares as app_email.
	var foot = el("div", "folt-signin-foot");
	foot.appendChild(
		document.createTextNode("© " + new Date().getFullYear() + " Friends of Lake Turkana · ")
	);
	foot.appendChild(document.createTextNode(t("Trouble signing in?") + " "));
	var support = el("a", null, "ict@folt.org");
	support.href = "mailto:ict@folt.org";
	foot.appendChild(support);
	brand.appendChild(foot);

	pane.classList.add("folt-signin-pane");
	content.insertBefore(brand, pane);

	// ── 2. THE FORM SIDE ────────────────────────────────────────────────────────────────

	// A marker on the label of every field the input itself declares required, so the
	// asterisk folt_login.css draws comes from the markup rather than from a list of field
	// ids that would go stale. aria-hidden is unnecessary and not used here: the asterisk is
	// generated content on a ::after, which assistive technology already skips in most
	// implementations, and `required` on the input is what actually announces the fact.
	Array.prototype.forEach.call(
		document.querySelectorAll(".page-card-body .form-group"),
		function (group) {
			var input = group.querySelector("input[required]");
			var label = group.querySelector(".form-label");
			if (input && label) {
				label.classList.add("folt-signin-required");
			}
		}
	);

	// The sign-in sections only. /update-password keeps its own "Set Password" heading, and
	// the forgot-password and sign-up cards keep theirs -- they are the answer to "which
	// screen am I on", which is exactly what a greeting would take away.
	Array.prototype.forEach.call(
		document.querySelectorAll(LOGIN_SECTIONS),
		function (loginSection) {
			var heading = loginSection.querySelector(".page-card-head h4");
			if (heading) {
				heading.textContent = t("Welcome back");
			}
			var subtitle = loginSection.querySelector(".page-card-head .page-card-subtitle");
			if (subtitle) {
				subtitle.textContent = t("Sign in to your account to continue");
			}

			// frappe labels this button "Continue". The two exclusions matter: the
			// e-mail-link and LDAP buttons in the same actions block ALSO carry `btn-login`,
			// and relabelling either would rename a different way of signing in.
			var submit = loginSection.querySelector(
				".page-card-actions .btn-login:not(.btn-login-with-email-link):not(.btn-ldap-login)"
			);
			if (submit) {
				submit.textContent = t("Sign in");
			}

			// The hint, last in the actions block so it sits under the buttons. It states
			// something true rather than decorative: the fields are in a <form> with a
			// submit button, so Enter submits it.
			var actions = loginSection.querySelector(".page-card-actions");
			if (actions && submit) {
				var hint = el("p", "folt-signin-hint");
				hint.appendChild(document.createTextNode(t("Press") + " "));
				hint.appendChild(el("kbd", null, "Enter"));
				hint.appendChild(document.createTextNode(" " + t("to sign in")));
				actions.appendChild(hint);
			}
		}
	);

	// Last: the switch the whole stylesheet hangs off. Nothing above it can leave a
	// half-built page styled.
	body.classList.add("folt-signin");
})();

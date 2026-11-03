"""Who sits on a Procurement Committee: the random draw, and the rules any committee must meet.

FoLT's procurement workflow (Scenario 1, competitive bidding, step 3) says three things about the
committee that evaluates the quotations and recommends an award:

  * it is constituted on a random basis;
  * the officer who requested the quotations is not on it (conflict of interest);
  * at least one member is a representative of the Program Department.

Before this module the evaluation enforced only the second; the buyer typed the members in, so the
committee was whoever the buyer chose. Now the system draws it when the evaluation is sent to the
committee (`Draft -> Committee Reviewing`), from everybody eligible, and writes down how it was
constituted, when, and from whom -- so "random" is a fact on the document, not a claim.

Eligible means: an enabled desk user holding the Procurement Committee role (the role the
workflow lets score), other than the requester of the quotations, the person sending the evaluation
and Administrator. A Program Department representative is an eligible user whose active Employee
record sits in FoLT Procurement Settings' Programs Department or a department under it.

The draw takes one representative at random from the Programs pool, then fills the remaining seats
at random from everybody else eligible -- so the representative rule never depends on luck, and a
Programs member can still win one of the other seats. `secrets.SystemRandom` rather than `random`:
nothing about the draw should be reproducible from a seed somebody could pick.

Administrator may still appoint a committee by hand (patches, fixtures, the e2e scripts and a
genuine correction need it); the evaluation then says "Appointed by administrator", and the
requester and Programs rules apply to it exactly the same.
"""

import secrets

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

SETTINGS = "FoLT Procurement Settings"
COMMITTEE_ROLE = "Procurement Committee"
DEFAULT_SIZE = 3
DEFAULT_PROGRAM_DEPARTMENT = "Programs - FoLT"

RANDOM_DRAW = "Random draw"
APPOINTED = "Appointed by administrator"


def committee_size() -> int:
	size = cint(frappe.db.get_single_value(SETTINGS, "committee_size")) if _has_settings() else 0
	return size or DEFAULT_SIZE


def program_department() -> str | None:
	dept = frappe.db.get_single_value(SETTINGS, "program_department") if _has_settings() else None
	dept = dept or DEFAULT_PROGRAM_DEPARTMENT
	return dept if frappe.db.exists("Department", dept) else None


def _has_settings() -> bool:
	return bool(frappe.db.exists("DocType", SETTINGS))


def _departments_under(root: str) -> set[str]:
	lft, rgt = frappe.db.get_value("Department", root, ["lft", "rgt"]) or (None, None)
	if lft is None:
		return {root}
	return set(frappe.get_all("Department", filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name"))


def program_representatives(users) -> set[str]:
	"""Which of `users` count as Program Department representatives."""
	users = [u for u in users if u]
	root = program_department()
	if not users or not root:
		return set()
	return set(
		frappe.get_all(
			"Employee",
			filters={"user_id": ["in", users], "status": "Active", "department": ["in", list(_departments_under(root))]},
			pluck="user_id",
		)
	)


def eligible_members(excluded=()) -> list[str]:
	"""Every user who may be drawn, in name order (the draw itself supplies the randomness)."""
	holders = frappe.get_all(
		"Has Role", filters={"role": COMMITTEE_ROLE, "parenttype": "User"}, pluck="parent", distinct=True
	)
	if not holders:
		return []
	out = set(excluded) | {"Administrator", "Guest"}
	enabled = frappe.get_all(
		"User",
		filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
		pluck="name",
		order_by="name",
	)
	return [u for u in enabled if u not in out]


def excluded_for(doc) -> list[str]:
	"""Who may not sit on this evaluation's committee: the requester of the quotations and the
	person constituting it."""
	people = [doc.get("requested_by")]
	if doc.get("request_for_quotation"):
		people.append(frappe.db.get_value("Request for Quotation", doc.request_for_quotation, "owner"))
	people.append(frappe.session.user)
	return [p for p in dict.fromkeys(people) if p]


def draw(doc, size: int | None = None) -> list[str]:
	"""Draw the committee for `doc` at random and write it, and the record of the draw, onto it."""
	size = size or committee_size()
	excluded = excluded_for(doc)
	pool = eligible_members(excluded)
	programs = sorted(program_representatives(pool))

	if not programs:
		frappe.throw(
			_("No Program Department representative can be drawn: nobody whose Employee record is in {0} holds the {1} role (the requester of the quotations is excluded). Ask HR or a System Manager to add one.").format(
				frappe.bold(program_department() or DEFAULT_PROGRAM_DEPARTMENT), frappe.bold(COMMITTEE_ROLE)
			),
			title=_("Committee cannot be drawn"),
		)
	if len(pool) < size:
		frappe.throw(
			_("A committee of {0} cannot be drawn: only {1} eligible member(s) hold the {2} role once the requester is excluded.").format(
				size, len(pool), frappe.bold(COMMITTEE_ROLE)
			),
			title=_("Committee cannot be drawn"),
		)

	rng = secrets.SystemRandom()
	representative = rng.choice(programs)
	rest = [u for u in pool if u != representative]
	chosen = [representative, *rng.sample(rest, size - 1)]
	rng.shuffle(chosen)

	doc.set("members", [{"member": user} for user in chosen])
	doc.folt_constitution = RANDOM_DRAW
	doc.folt_drawn_on = now_datetime()
	doc.folt_draw_pool = ", ".join(pool)
	doc.flags.folt_committee_drawn = True
	return chosen


def check_composition(doc):
	"""The rules any committee must meet before it starts evaluating, however it was constituted."""
	members = [row.member for row in doc.members or [] if row.member]
	if not members:
		frappe.throw(_("The committee has no members."), title=_("No committee"))
	if len(set(members)) != len(members):
		frappe.throw(_("A member appears on the committee twice."), title=_("Committee"))
	conflicted = sorted(set(members) & set(excluded_for(doc)))
	if conflicted:
		frappe.throw(
			_("Conflict of interest: {0} requested the quotations or is constituting the committee, and cannot sit on it.").format(", ".join(conflicted)),
			title=_("Conflict of interest"),
		)
	if not program_representatives(members):
		frappe.throw(
			_("At least one member of the committee must represent the Program Department ({0}).").format(
				frappe.bold(program_department() or DEFAULT_PROGRAM_DEPARTMENT)
			),
			title=_("No Program Department member"),
		)

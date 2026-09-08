import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from folt_customizations.participants import (
	ACKNOWLEDGED_FORMS,
	ELIGIBLE_CATEGORIES,
	PARTICIPANT_CATEGORIES,
	check_duplicates,
	normalise_mobile,
)

# A roster is a convenience, not a licence to paste the participant master into a document.
# The worked case ran to 57 attendees on one activity, so 200 leaves generous room while still
# refusing to build something nobody could check.
ROSTER_LIMIT = 200

# The identity fields the participant master owns -- the same set the form script pulls when a
# row is picked by hand (activity_participant_list.js PARTICIPANT_PROFILE_FIELDS), so a rostered
# row and a hand-picked one are the same person either way.
ROSTER_PROFILE_FIELDS = (
	"participant_name",
	"mobile_number",
	"id_number",
	"location",
	"gender",
	"is_pwd",
	"photo_consent",
)


class ActivityParticipantList(Document):
	"""The attendance register — the source record for who took part in an activity.

	Specified as W-04A in Module 1 Annex A. Nothing may be reimbursed that is not on a
	verified register for the same project, so the validation here is what makes the
	reimbursement list safe to derive.
	"""

	def validate(self):
		self.normalise_rows()
		check_duplicates(self.participants or [], _("attendance register"))
		self.link_or_create_participants()
		self.set_totals()

	def before_submit(self):
		if not self.participants:
			frappe.throw(_("A register cannot be verified with no attendees."))

		# A roster is not a register. Once a register can be filled from a previous session
		# before the activity happens, "nobody is ticked" stops meaning "empty document" and
		# starts meaning "the sheet has not come back yet" -- and verifying it would put a
		# register of expectations where the reimbursement list looks for a record of fact.
		if not any(row.attended for row in self.participants):
			frappe.throw(
				_(
					"Nobody on this register is marked as having attended. If these rows are a roster "
					"printed for signing, reconcile the signed sheet first -- tick who signed and mark "
					"the rest absent."
				),
				title=_("Roster not reconciled"),
			)

		# Warned, not blocked -- deliberately, and for the same reason the missing scan below is.
		# Somebody who was in the room is an attendee whether or not the cell against their name
		# got filled in; F-04-E4 puts the gate on the payment, not on the register.
		unresolved = [row for row in self.participants if row.attended and not row.acknowledgement]
		if unresolved:
			frappe.msgprint(
				_(
					"{0} of {1} attendees have no acknowledgement recorded. Reconcile the signed sheet "
					"if it has come back."
				).format(len(unresolved), len(self.participants)),
				title=_("Acknowledgement not recorded"),
				indicator="orange",
			)

		# The signed sheet is asked for, not demanded. It used to be a throw here, and what that
		# cost was the ordinary case: the activity happened, the attendees are keyed in and
		# correct, and the scanned sheet is still in somebody's phone or with the field officer.
		# Blocking verification on it stopped the whole chain -- the reimbursement list derives
		# from a *verified* register -- over a document that changes nothing about who attended.
		# So it is a standing reminder on a register that carries no sheet, and the checklist on
		# the form says the same thing before anyone presses the button (document_guide.DOCUMENTS).
		if not self.attendance_sheet:
			frappe.msgprint(
				_("This register is being verified without the signed attendance sheet. Attach it when it arrives — the field stays editable after verification."),
				title=_("No signed sheet on file"),
				indicator="orange",
			)

	def normalise_rows(self):
		for row in self.participants or []:
			row.mobile_number = normalise_mobile(row.mobile_number, label=row.participant_name)

			# Category drives eligibility, but the flag stays editable so that excluding
			# someone is a recorded decision rather than a silent omission (F-04-D2).
			if row.get("__islocal") or row.eligible_for_reimbursement is None:
				row.eligible_for_reimbursement = 1 if row.category in ELIGIBLE_CATEGORIES else 0

			# An absent person has no mark against their name. This is normalisation, not a
			# judgement: `acknowledgement` DEFAULTS to "Signature" on a new row, so every row
			# somebody marks absent -- in the grid, or by leaving it unticked in the reconcile
			# dialog -- would otherwise read "absent, and signed", which is a contradiction the
			# document would then carry into the reimbursement list. Refusing it instead was
			# tried and was worse: it fires on the *default*, so unticking a box threw an error
			# naming a signature nobody had claimed. "None" is the honest value, and it is what
			# the reconcile dialog writes for the same rows.
			if not row.attended and row.acknowledgement in ACKNOWLEDGED_FORMS:
				row.acknowledgement = "None"

			# `attended` deliberately does NOT zero eligibility here, and that is a fix rather
			# than an omission. A row that arrives unattended -- a roster printed for signing, or
			# an imported sheet row whose signature cell was blank -- would have had eligibility
			# forced to 0 permanently: the branch above only re-derives from the category while
			# the row is new or the value is None, so ticking somebody present once the signed
			# sheet came back left them ineligible for ever, and the reimbursement list skipped
			# them as "not eligible by category". Nobody would have been paid.
			#
			# Absence is applied where it is read instead, which is everywhere that matters:
			# set_totals ANDs with `attended`, and so do fetch_participants and
			# get_verified_attendees on the reimbursement list.

	def link_or_create_participants(self):
		"""Match each attendee to the participant master, creating it where new.

		Matching is on the mobile number, so a person who returns to a second activity
		is the same record rather than a re-keyed string (F-04A-V4).
		"""
		for row in self.participants or []:
			if row.participant:
				continue

			if not row.mobile_number:
				# A cash payee with no mobile destination is permitted but has no natural
				# key, so no master record is created for them here (F-04A-E1).
				continue

			existing = frappe.db.get_value(
				"FoLT Participant", {"mobile_number": row.mobile_number}, "name"
			)

			if existing:
				row.participant = existing
				continue

			participant = frappe.get_doc(
				{
					"doctype": "FoLT Participant",
					"participant_name": row.participant_name,
					"mobile_number": row.mobile_number,
					"id_number": row.id_number,
					"location": row.location,
					"gender": row.gender,
					"is_pwd": row.is_pwd,
					"photo_consent": row.photo_consent,
				}
			).insert(ignore_permissions=True)

			row.participant = participant.name

	def set_totals(self):
		rows = self.participants or []
		self.total_attendees = sum(1 for row in rows if row.attended)
		self.total_eligible = sum(1 for row in rows if row.attended and row.eligible_for_reimbursement)

	def on_update_after_submit(self):
		# Only a change to WHO attended can orphan a payee. Now that the signed sheet may be
		# attached after verification, an edit that touches nothing but the evidence is the
		# ordinary case, and warning about derived lists there would train people to dismiss the
		# warning that matters.
		if self.attendees_changed():
			self.revalidate_derived_lists()

	def attendees_changed(self) -> bool:
		before = self.get_doc_before_save()
		if not before:
			return True

		def shape(doc):
			return [
				(row.participant, row.participant_name, row.mobile_number, row.attended, row.eligible_for_reimbursement)
				for row in doc.participants or []
			]

		return shape(before) != shape(self)

	def on_cancel(self):
		self.revalidate_derived_lists(cancelled=True)

	def revalidate_derived_lists(self, cancelled=False):
		"""Flag reimbursement lists already derived from this register (F-04A-E6).

		Amending a register after money has been budgeted against it can orphan a payee,
		so anything derived from it is surfaced rather than left to be discovered at
		retirement.
		"""
		derived = frappe.get_all(
			"Participant Reimbursement List",
			filters={"attendance_reference": self.name, "docstatus": ["<", 2]},
			pluck="name",
		)

		if not derived:
			return

		frappe.msgprint(
			_("This register has changed. Re-check the reimbursement lists derived from it: {0}").format(
				", ".join(frappe.utils.get_link_to_form("Participant Reimbursement List", d) for d in derived)
			),
			title=_("Cancelled register") if cancelled else _("Register amended"),
			indicator="orange",
		)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_verified_registers(doctype, txt, searchfield, start, page_len, filters):
	"""Link query used by the reimbursement list: verified registers, this project only.

	The filters are handed straight to `get_all` rather than pasted into SQL, and that is the
	whole point of the shape of this function. A link field does not post the filters the form
	script set: frappe normalises each one into an `[operator, value]` pair on its way to
	`search_link`, so `{"activity": "PROJ-0005"}` arrives as `{"activity": ["=", "PROJ-0005"]}`.
	Bound into a query as a parameter, a two-element list is rendered by MySQLdb as a row
	constructor and MariaDB rejects the comparison outright -- "Illegal parameter data types
	varchar and row for operation '='" -- so picking a register failed with a traceback rather
	than a wrong result. `get_all` is built for that form and takes either.

	`docstatus` is set last and deliberately overrides whatever arrived: this query exists to
	offer verified registers, and a draft one must never reach the dropdown even if the caller
	asks for it. Nothing may be reimbursed that is not on a verified register (see the class
	docstring), and this is where that starts.
	"""
	applied = dict(filters or {})
	applied["docstatus"] = 1

	return frappe.get_all(
		"Activity Participant List",
		fields=["name", "activity_title", "session_date"],
		filters=applied,
		or_filters=[["name", "like", f"%{txt}%"], ["activity_title", "like", f"%{txt}%"]] if txt else None,
		order_by="session_date desc",
		start=start,
		page_length=page_len,
		as_list=True,
	)


@frappe.whitelist()
def get_roster_sources(register: str) -> list[dict]:
	"""Earlier registers on this project that a roster could be copied from.

	Returned rather than left to a bare Link field so the dialog can offer real sessions with
	their dates and headcounts -- "12 Aug 2026, 34 attendees" is a choice somebody can make,
	"APL-2026-00011" is a guess. An empty list is also the signal the button uses to hide
	itself, because the first register of an activity has nothing to copy from.
	"""
	frappe.has_permission("Activity Participant List", doc=register, throw=True)

	activity = frappe.db.get_value("Activity Participant List", register, "activity")
	if not activity:
		return []

	return frappe.get_all(
		"Activity Participant List",
		filters={"activity": activity, "docstatus": ["<", 2], "name": ["!=", register]},
		fields=["name", "session_date", "venue", "total_attendees", "docstatus"],
		order_by="session_date desc, creation desc",
		limit=20,
	)


@frappe.whitelist()
def autofill_roster(
	register: str,
	from_register: str,
	attended_only: int = 1,
	limit: int = ROSTER_LIMIT,
) -> dict:
	"""Append an earlier session's attendees to this draft register as a roster.

	This is the half of W-04A that the chain never had. `make_attendance_register` opens the
	register headed and empty because who turned up is discovered on the day -- but for a
	multi-session activity the *roster* is known in advance, and typing it again for every
	session was the largest piece of pure re-keying left in the finance chain.

	Rows land unattended on purpose. See the comment on the append below: a roster is who is
	expected, and the sheet has not come back yet.
	"""
	doc = frappe.get_doc("Activity Participant List", register)
	doc.check_permission("write")

	if doc.docstatus != 0:
		frappe.throw(
			_("A roster can only be added to a draft register. The attendees on a verified register are the record."),
			title=_("Register already verified"),
		)

	limit = min(cint(limit) or ROSTER_LIMIT, 500)

	filters = {"parenttype": "Activity Participant List", "parent": from_register}
	if cint(attended_only):
		filters["attended"] = 1

	# Read the child table directly rather than loading the source register as a document: a
	# 200-row register pulled in full to copy seven fields per row is waste, and this is the
	# same shape get_verified_attendees already uses. `limit + 1` is what makes `truncated`
	# truthful -- fetching exactly `limit` cannot tell "200 candidates" from "200 of 900".
	candidates = frappe.get_all(
		"Activity Participant Entry",
		filters=filters,
		fields=["participant", "category", *ROSTER_PROFILE_FIELDS],
		order_by="idx asc",
		limit=limit + 1,
	)

	truncated = len(candidates) > limit
	candidates = candidates[:limit]

	# Dedupe on BOTH keys. A walk-in typed in by hand has a mobile number but no participant
	# link yet, so matching on the link alone would re-add them -- and check_duplicates would
	# then throw on save, which is a worse way to find out (F-04A-E2).
	seen_participants = {row.participant for row in doc.participants if row.participant}
	seen_mobiles = {row.mobile_number for row in doc.participants if row.mobile_number}

	added = skipped_existing = 0

	for candidate in candidates:
		participant = candidate.get("participant")
		mobile = candidate.get("mobile_number")

		if (participant and participant in seen_participants) or (mobile and mobile in seen_mobiles):
			skipped_existing += 1
			continue

		doc.append(
			"participants",
			{
				**{field: candidate.get(field) for field in ROSTER_PROFILE_FIELDS},
				"participant": participant,
				"category": candidate.get("category") or "Community Participant",
				# A roster is who is EXPECTED. Nobody has signed anything yet, so the row lands
				# unattended and unacknowledged -- `attended` is what set_totals counts, and a
				# register claiming 40 attendees before the sheet came back would be a lie the
				# reimbursement list is allowed to derive from. "" rather than "None" because
				# "None" is a finding: it means somebody read the sheet and recorded that this
				# person left no mark.
				"attended": 0,
				"acknowledgement": "",
				# eligible_for_reimbursement is deliberately NOT set here. Document.append marks
				# the row __islocal, so normalise_rows derives it from the category on save --
				# one code path for a rostered row and a hand-typed one.
			},
		)

		if participant:
			seen_participants.add(participant)
		if mobile:
			seen_mobiles.add(mobile)
		added += 1

	doc.save()

	source_activity = frappe.db.get_value("Activity Participant List", from_register, "activity")

	return {
		"added": added,
		"skipped_existing": skipped_existing,
		"candidates": len(candidates),
		"truncated": truncated,
		"roster_rows": len(doc.participants),
		"limit": limit,
		# Allowed, not refused: F-04A-V4 is precisely about a participant recurring across
		# activities. Surfaced so the preparer knows the roster came from somewhere else.
		"cross_project": bool(source_activity and source_activity != doc.activity),
	}


@frappe.whitelist()
def read_attendance_sheet(register: str, file_url: str | None = None) -> dict:
	"""Read the attached sheet into candidate rows. WRITES NOTHING.

	Returns rows shaped for the `participants` grid so the form script can drop them in
	unsaved: the preparer then reviews them in the real grid, with all of its validation, and
	saves when they are right. That is the whole reason this does not insert -- an OCR'd row is
	a proposal, and a register is what the reimbursement list derives from.

	Refuses a handwritten sheet outright rather than importing rubbish. FoLT's participants list
	is a pre-printed blank form filled in by hand, and its numbers OCR at 0% -- see
	`sheet_import`. What it still reports in that case is the count of signature marks, because
	ink detection works on handwriting perfectly well even when the writing does not: that gives
	a headcount to check the typed-in register against (F-04A-E5).
	"""
	from folt_customizations import sheet_import

	doc = frappe.get_doc("Activity Participant List", register)
	doc.check_permission("write")

	if doc.docstatus != 0:
		frappe.throw(
			_("A sheet can only be read into a draft register. The attendees on a verified register are the record."),
			title=_("Register already verified"),
		)

	parsed = sheet_import.parse(file_url or doc.attendance_sheet)

	if parsed["handwritten"]:
		frappe.throw(
			_(
				"This sheet is filled in by hand, and handwritten names and mobile numbers cannot be "
				"read reliably enough to pay anyone from — {0} of the rows produced a usable number. "
				"It does show <b>{1} row(s) with a signature or thumbprint</b>, so use that as the "
				"headcount to check against.<br><br>To stop this happening next time: fill the register "
				"from a previous session with <b>Autofill roster</b>, print the <b>FoLT Attendance "
				"Sheet</b>, and the names go to the activity already on the paper."
			).format(
				sum(1 for r in parsed["rows"] if _valid_mobile(r.get("mobile_number"))),
				parsed["signed_marks"],
			),
			title=_("Handwritten sheet"),
		)

	# What is already on the register, so a second read of the same sheet is a no-op rather than
	# a duplicate. Both keys, because a typed walk-in has a number but no participant link.
	seen_participants = {row.participant for row in doc.participants if row.participant}
	seen_mobiles = {row.mobile_number for row in doc.participants if row.mobile_number}

	rows, skipped_existing, skipped_blank = [], 0, 0
	in_sheet: set[str] = set()

	for parsed_row in parsed["rows"]:
		name = (parsed_row.get("participant_name") or "").strip()
		mobile = parsed_row.get("mobile_number") or ""
		flags: list[str] = []

		if not name:
			skipped_blank += 1
			continue

		if mobile and mobile in in_sheet:
			flags.append(_("appears more than once on the sheet"))
			mobile = ""
		elif mobile:
			in_sheet.add(mobile)

		if mobile and mobile in seen_mobiles:
			skipped_existing += 1
			continue

		# An unreadable number is NOT carried into the row. normalise_mobile throws on save, and
		# one smudged digit must not block the other fifty-three rows -- so the raw reading goes
		# into remarks for the preparer to key from the paper, and the field is left empty.
		remarks: list[str] = []
		if mobile and not _valid_mobile(mobile):
			remarks.append(_("Mobile read as {0} — could not be read; check it against the sheet.").format(mobile))
			flags.append(_("unreadable mobile number"))
			mobile = ""
		elif not mobile:
			flags.append(_("no mobile number"))

		participant = None
		if mobile:
			match = frappe.db.get_value(
				"FoLT Participant", {"mobile_number": mobile}, ["name", "participant_name"], as_dict=True
			)
			if match:
				participant = match.name
				# F-04A-V4 matches on number AND name. A number that belongs to somebody else on
				# file is the case where a single misread digit sends money to a stranger, so it
				# is surfaced rather than silently linked.
				if not _same_person(match.participant_name, name):
					flags.append(
						_("number is on file for {0}").format(match.participant_name)
					)
					remarks.append(
						_("Sheet says {0}; {1} already holds {2}. Confirm which is right before paying.").format(
							name, match.participant_name, mobile
						)
					)
					participant = None

		category = _match_category(parsed_row.get("category"))
		if parsed_row.get("category") and not category:
			flags.append(_("category {0} not recognised").format(parsed_row["category"]))

		location = _match_location(parsed_row.get("location"))
		if parsed_row.get("location") and not location:
			flags.append(_("location {0} not on file").format(parsed_row["location"]))

		# The arithmetic cross-check. A row whose printed total disagrees with its own components
		# was misread somewhere, and that is worth saying even on the register, where the amounts
		# are not stored -- it is evidence the row is not trustworthy.
		parts = sum(parsed_row.get(k) or 0 for k in ("transport", "sustenance", "accommodation"))
		if parsed_row.get("total") is not None and parts and parsed_row["total"] != parts:
			flags.append(_("printed total {0} does not match {1}").format(parsed_row["total"], parts))

		acknowledgement = parsed_row.get("acknowledgement") or ""
		rows.append(
			{
				"participant": participant,
				"participant_name": name,
				"mobile_number": mobile,
				"location": location,
				"category": category or "Community Participant",
				"attended": 1 if acknowledgement in ACKNOWLEDGED_FORMS else 0,
				"acknowledgement": acknowledgement,
				"id_number": parsed_row.get("id_number") or "",
				"gender": parsed_row.get("gender") or "",
				"remarks": " ".join(remarks),
				"_flags": flags,
			}
		)

	return {
		"rows": rows,
		"source": parsed["source"],
		"pages": parsed["pages"],
		"signed_marks": parsed["signed_marks"],
		"skipped_existing": skipped_existing,
		"skipped_blank": skipped_blank,
		"flagged": sum(1 for r in rows if r["_flags"]),
		"warnings": parsed["warnings"],
	}


def _valid_mobile(number: str | None) -> bool:
	"""Shape check only -- deliberately not normalise_mobile, which throws.

	The importer has to survive a bad cell and keep going, so validity is a question here, not
	an exception. normalise_mobile still runs on save, where throwing and naming the person is
	the right behaviour.
	"""
	import re

	return bool(number and re.fullmatch(r"0(7|1)[0-9]{8}", number))


def _same_person(a: str | None, b: str | None) -> bool:
	"""Whether two readings of a name plausibly name the same person.

	Compared as a set of words, so "ANN KAPENGI" and "Kapengi Ann" match and a middle name
	dropped by whoever typed the sheet does not raise a false alarm. Anything sharing no words
	at all is a different person.
	"""
	words_a = {w for w in (a or "").upper().split() if len(w) > 1}
	words_b = {w for w in (b or "").upper().split() if len(w) > 1}
	if not words_a or not words_b:
		return False
	return bool(words_a & words_b)


def _match_category(text: str | None) -> str | None:
	"""Resolve a sheet's category text to one of PARTICIPANT_CATEGORIES, or nothing.

	Matters more than it looks: category is what `normalise_rows` derives
	`eligible_for_reimbursement` from, so importing every row as "Community Participant" would
	quietly mark the county officials and FoLT staff on a sheet as payable. Matched loosely
	(substring, either direction) because a sheet writes "Staff" or "County Official" where the
	Select says "FoLT Staff" and "County Official".
	"""
	text = " ".join((text or "").split()).lower()
	if not text:
		return None

	for option in PARTICIPANT_CATEGORIES:
		if option.lower() == text:
			return option
	for option in PARTICIPANT_CATEGORIES:
		low = option.lower()
		if low in text or text in low:
			return option
	return None


def _match_location(text: str | None) -> str | None:
	"""Resolve a sheet's location text to a FoLT Location, or nothing.

	Never creates one. A location invented from a misread cell would quietly pollute the master
	that the rate schedule is keyed on, and an unmatched location is a blank field plus a flag --
	which the preparer fixes in one click from the grid's own link picker.
	"""
	text = " ".join((text or "").split())
	if not text:
		return None

	exact = frappe.db.get_value("FoLT Location", {"name": text}, "name")
	if exact:
		return exact

	like = frappe.get_all("FoLT Location", filters={"name": ["like", text]}, pluck="name", limit=1)
	return like[0] if like else None

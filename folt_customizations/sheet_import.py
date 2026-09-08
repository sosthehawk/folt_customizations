"""Read a FoLT participant sheet back into the system.

FoLT's accountability packs carry two sheets that look alike and are not:

* the **participants list** (the W-04A attendance register) is a pre-printed blank form filled
  in BY HAND at the activity, and
* the **transport reimbursement sheet** is typed in Word afterwards from that handwriting.

Only the second one can be read. Measured with tesseract 5.3 over the real pack
(`Finance Workflow Example.pdf`, the KES 262,000 float): the typed sheets score 95% on names and
90.5% on phone numbers, while the handwritten registers score 38% and **0%**. Nought per cent on
phone numbers is not a quality problem, it is a different answer -- a phone number is where the
money goes, so a sheet whose numbers cannot be read carries no attendee identity at all. This
module therefore refuses handwriting explicitly (see `_looks_handwritten`) rather than importing
rows nobody can trust, and points at the pre-printed sheet instead.

Nothing here leaves the FoLT stack. tesseract runs in this container, called as a binary, and no
scan is sent to any external service -- these sheets carry names, mobile numbers and ID numbers.
Do not "upgrade" this to a cloud vision API.

Two things make OCR safe enough to act on, and both come from the sheet itself rather than from
the recogniser:

* **TOTAL is a checksum.** The sheet prints a per-row total; the controller computes
  `sum(transport, sustenance, accommodation)`. A disagreement localises a misread digit to one
  cell. In the pack this caught totals read as `40003` and `50009`, both signature ink bleeding
  across the cell border.
* **A mobile number has a shape.** `0(7|1)\\d{8}` rejected `015448604` and `43003240` outright.
  What it cannot catch -- a wrong digit that still forms a valid number -- is why every row is
  matched against `FoLT Participant` and why nothing is written without review.

This is the app's first module to read an attachment's bytes and its first to import a
third-party package (`pypdfium2`, `PIL`, `pdfplumber` -- all already in the bench image; the app
still declares `dependencies = []`).
"""

import os
import re
import subprocess

import frappe
from frappe import _

# Below this a pixel counts as ink; above it, as blank paper. Wide gap on purpose -- these are
# scans, so mid-greys are paper texture and JPEG ringing rather than content.
INK = 170
PAPER = 200

# Render PDF pages at this DPI. 300 is what the measurements above were taken at; 150 loses the
# thin ruled lines that the whole grid detection depends on.
RENDER_DPI = 300

# A cell is only OCR'd after being inset by this many pixels, to keep the ruled border out of the
# crop -- a black line through a glyph is what turns 5000 into 50009.
CELL_INSET = 5

# Guard rails. A sheet with more rows than this is not a session register, and a run over more
# pages than this is somebody importing the whole accountability pack by mistake.
MAX_ROWS = 400
MAX_PAGES = 20

# The second, digit-whitelisted OCR pass only re-reads cells the first pass got demonstrably
# wrong. Capped so a badly skewed page cannot turn into hundreds of subprocess calls.
MAX_REPAIRS = 60

# Fewer than this share of rows yielding a structurally valid Kenyan mobile means the sheet is
# handwritten. Measured separation is 90.5% (typed) against 0% (handwritten), so anywhere in the
# middle is safe; this sits low enough that a genuinely typed sheet with a bad scan still gets
# imported and flagged rather than refused.
READABLE_PHONE_SHARE = 0.4

MOBILE_RE = re.compile(r"^0(7|1)\d{8}$")

# Header synonyms. A field sheet is written by whoever made it, so the column is found by what it
# is called rather than by position -- the pack alone has sheets with and without an S/No column.
COLUMN_SYNONYMS = {
	"serial": ("s/no", "s/n", "s / no", "sno", "no.", "#"),
	"participant_name": ("name", "names", "participant", "attendee", "jina"),
	# The sheet's own category drives eligibility on the register (ELIGIBLE_CATEGORIES), so a
	# sheet that distinguishes staff from community participants must be believed rather than
	# have every row defaulted to "Community Participant".
	"category": ("category", "cadre", "designation"),
	"location": ("location", "ward", "village", "sub-county", "subcounty", "area"),
	"mobile_number": ("phone", "mobile", "tel", "simu", "number", "contact"),
	"id_number": ("id no", "id number", "id/no", "national id", "idno"),
	"gender": ("gender", "sex", "m/f"),
	"is_pwd": ("pwd", "disability", "disabled"),
	"photo_consent": ("photo consent", "photo", "consent"),
	"transport": ("transport", "fare"),
	"sustenance": ("sustenance", "lunch", "meals", "refreshment"),
	"accommodation": ("accommodation", "accomodation", "lodging"),
	"total": ("total", "amount", "sum"),
	"acknowledgement": ("signature", "sign", "acknowledgement", "acknowledgment", "thumbprint"),
}

# Order matters: "phone number" contains "number", and "total" is a substring of nothing but
# itself, so the more specific keys are matched first.
COLUMN_ORDER = (
	"serial", "participant_name", "category", "location", "mobile_number", "id_number", "gender",
	"is_pwd", "photo_consent", "transport", "sustenance", "accommodation", "total",
	"acknowledgement",
)

# Enough of these in one band and it is the header row. Three, because a sheet may legitimately
# lack the serial, the ID and the consent columns.
HEADER_HITS_REQUIRED = 3

# Page segmentation for the one whole-page pass. Measured on the real sheets, and 6 wins clearly:
# on the sample it finds 116 words against 70 for psm 11 and 12, and 99 for psm 4 -- and the
# difference decides whether the header row is read at all. The sparse-text modes sound like the
# right fit for a table and are not; they skip most of the grid.
PAGE_PSM = "6"

# Segmentation for the per-cell repair pass: one line of text, which is what a cell holds.
CELL_PSM = "7"

# How much of the sharpest rule on the page a line has to match to count as a rule itself, and
# an absolute floor for a page whose sharpest rule is faint.
#
# 0.40 rather than something nearer 1.0 because a rule is not uniformly dark. The outer border
# of a table, and the line under a header row, are two collapsed borders and come out ~0.86 of
# the width; the single hairline between two body rows comes out ~0.47 on the same page, since
# at 300 DPI a 0.7px CSS border is about two device pixels and a scanline catches the
# anti-aliased half of it as often as the solid half. FoLT's own printed attendance sheet sat at
# 0.465 against a 0.471 threshold and lost every row separator by that margin.
#
# The headroom below is what makes this safe: a scanline through a row of TEXT measures 0.11 to
# 0.13 on the same sheets, so there is a wide gap between "line" and "writing". Detecting a few
# extra bands costs nothing either way -- the header is found by what it says, and a band with
# no name and no number in it is skipped.
RULE_SHARE = 0.40
RULE_FLOOR = 0.28

# Words within this many pixels of each other vertically are on the same line of a cell, and are
# then ordered left to right. Too small and a single line whose baseline wobbles by a few pixels
# gets split into two, which silently reverses a name -- "JECINTA EPEYON" came back as "EPEYON
# JECINTA" at 12. Too large and a genuinely two-line cell interleaves.
LINE_BUCKET = 28

# A band between two horizontal rules is only a table row if the table's vertical rules are
# actually present at that height. Without this test the signature block below the table reads
# as one more attendee called "Prepared By Administrator" -- it sits between two rules, because
# the underlines of the signoff are rules too.
IN_TABLE_SHARE = 0.6


# --- getting at the bytes -------------------------------------------------------------------


def _file_content(file_url: str) -> tuple[bytes, str]:
	"""Resolve an attachment URL to its bytes and lowercased extension.

	Goes through the File doctype rather than joining the URL onto the site path by hand. That
	is not politeness: `/private/files/...` lives somewhere else entirely, and a hand-built path
	is how a traversal gets in. `get_content` also applies the File's own read permission.
	"""
	if not file_url:
		frappe.throw(_("No document is attached to read."), title=_("Nothing to import"))

	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		# An Attach field can hold a URL whose File row was deleted, which is a broken
		# attachment rather than a missing feature -- say which.
		frappe.throw(
			_("The attached file {0} is not in the file store any more. Re-attach it and try again.").format(
				frappe.bold(file_url)
			),
			title=_("Attachment missing"),
		)

	content = frappe.get_doc("File", name).get_content()
	if isinstance(content, str):
		content = content.encode("utf-8", "replace")

	return content, os.path.splitext(file_url.split("?")[0])[1].lower()


def _pages_from_pdf(content: bytes):
	"""Render each PDF page to a greyscale PIL image."""
	import pypdfium2 as pdfium

	pdf = pdfium.PdfDocument(content)
	count = min(len(pdf), MAX_PAGES)
	for i in range(count):
		yield pdf[i].render(scale=RENDER_DPI / 72).to_pil().convert("L")


def _tables_from_text_pdf(content: bytes) -> list[list[list[str]]]:
	"""Tables from a PDF that still has a text layer -- exact, and no OCR involved.

	Worth trying every time: if somebody attaches the Word export rather than a scan of the
	printout, this path reads the sheet perfectly and none of the machinery below runs. Page 1 of
	the real accountability pack is exactly that.
	"""
	import pdfplumber
	import io

	tables = []
	with pdfplumber.open(io.BytesIO(content)) as pdf:
		for page in pdf.pages[:MAX_PAGES]:
			for table in page.extract_tables() or []:
				if len(table) >= 2:
					tables.append([[(cell or "").replace("\n", " ").strip() for cell in row] for row in table])
	return tables


def _tables_from_spreadsheet(content: bytes, ext: str) -> list[list[list[str]]]:
	"""Rows from a real spreadsheet, via frappe's own readers."""
	if ext == ".csv":
		from frappe.utils.csvutils import read_csv_content

		rows = read_csv_content(content.decode("utf-8", "replace"))
	else:
		from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

		rows = read_xlsx_file_from_attached_file(fcontent=content)

	rows = [[str(cell).strip() if cell is not None else "" for cell in row] for row in (rows or [])]
	return [rows] if rows else []


# --- the image pipeline ---------------------------------------------------------------------
#
# Order is load-bearing: crop, then deskew, then find the rules. Getting it wrong does not
# degrade the result, it produces nothing at all -- the first version of this skipped deskew and
# detected zero horizontal rules on a sheet whose grid is perfectly clear to the eye.


def _crop_to_paper(img):
	"""Trim the dark surround a scanner bed or a screenshot leaves around the sheet.

	Without it the surround counts as ink on every scanline and swamps the line detection below
	(the sample screenshot's corner pixel is 46, i.e. near black).
	"""
	w, h = img.size
	px = img.load()
	rows = [y for y in range(0, h, 3) if sum(1 for x in range(0, w, 8) if px[x, y] > PAPER) > (w / 8) * 0.5]
	cols = [x for x in range(0, w, 3) if sum(1 for y in range(0, h, 8) if px[x, y] > PAPER) > (h / 8) * 0.5]

	if not rows or not cols:
		return img
	return img.crop((cols[0], rows[0], cols[-1], rows[-1]))


def _rule_peak(img) -> float:
	"""Share of the width crossed by the single most ink-heavy scanline.

	This is the deskew objective function. On a square sheet a ruled line lights up one scanline
	almost completely; tilted, the same line is spread over many and none of them stands out.
	"""
	px = img.load()
	w, h = img.size
	return max(sum(1 for x in range(0, w, 3) if px[x, y] < INK) for y in range(h)) / (w / 3)


def _deskew(img):
	"""Rotate to whichever angle makes the ruled lines sharpest.

	Half a degree matters more than it sounds: over 2400px of width it drags a 2px rule down
	through ~20 rows of pixels, so no scanline crosses more than about 40% of it and the grid is
	invisible to a projection profile. Correcting it takes the peak from ~0.41 to ~0.87 on the
	sample. Real sheets in the pack sit between 0.05 and 0.85 degrees.

	Coarse pass then fine pass, rather than one sweep at 0.05 degrees, because rotation is what
	costs here and 34 rotations answer it as well as 160 would.

	The angle is SEARCHED on a half-scale copy and applied once at full size. Rotation cost goes
	with area, and searching at full resolution was 3.1s of a 4.0s page -- by far the most
	expensive thing this module did, on a job that runs inside a web request. Half scale is
	still ample to see the angle: what is being measured is whether a rule lands on one scanline
	or several, and a rule stays a rule at 1240px wide.
	"""
	from PIL import Image

	scale = 2 if max(img.size) > 1600 else 1
	probe = img.resize((img.width // scale, img.height // scale), Image.BILINEAR) if scale > 1 else img

	best = (0.0, _rule_peak(probe))
	for step, span in ((0.25, 8), (0.05, 5)):
		centre = best[0]
		for i in range(-span, span + 1):
			angle = round(centre + i * step, 2)
			if angle == best[0] and i != 0:
				continue
			peak = _rule_peak(probe.rotate(angle, resample=Image.BILINEAR, fillcolor=255))
			if peak > best[1]:
				best = (angle, peak)

	angle = best[0]
	if angle:
		img = img.rotate(angle, resample=Image.BILINEAR, fillcolor=255)

	# Re-measure at full size. The probe's peak is only a comparator between angles; the value
	# that feeds the rule threshold has to come from the image the rules are found in.
	return img, angle, _rule_peak(img)


def _rules(img, *, axis: int, min_frac: float) -> list[int]:
	"""Positions of the ruled lines along one axis, as pixel runs collapsed to midpoints."""
	px = img.load()
	w, h = img.size

	if axis == 0:
		hits = [y for y in range(h) if sum(1 for x in range(0, w, 2) if px[x, y] < INK) / (w / 2) >= min_frac]
	else:
		hits = [x for x in range(w) if sum(1 for y in range(0, h, 2) if px[x, y] < INK) / (h / 2) >= min_frac]

	grouped, run = [], []
	for i in hits:
		if run and i - run[-1] > 6:
			grouped.append(sum(run) // len(run))
			run = []
		run.append(i)
	if run:
		grouped.append(sum(run) // len(run))

	return grouped


def _in_table(img, verticals: list[int], y: int) -> bool:
	"""Whether the table's column rules are still present at this height.

	The page has ruled lines below the table too -- the signature block's underlines -- so a
	band between two rules is not proof of a row. The columns are, because they stop where the
	table stops.
	"""
	px = img.load()
	w, h = img.size
	if not 0 <= y < h:
		return False

	hits = 0
	for x in verticals:
		# The rule may sit a pixel either side after rotation, so look in a small window.
		if any(0 <= x + dx < w and px[x + dx, y] < INK for dx in (-2, -1, 0, 1, 2)):
			hits += 1

	return hits / max(1, len(verticals)) >= IN_TABLE_SHARE


def _ink_share(img, box) -> float:
	"""Fraction of a cell that carries ink -- how the signature column is read.

	Never OCR'd. A signature is not text and a thumbprint certainly is not; the only question
	worth asking of that cell is whether anybody made a mark in it, and that is a pixel count.
	Deliberately does not try to tell a signature from a thumbprint: a thumbprint is a dense
	blob and a signature is thin strokes, but the overlap is wide enough that guessing would be
	worse than letting the preparer say.
	"""
	x0, y0, x1, y1 = box
	crop = img.crop((x0 + 8, y0 + 8, x1 - 8, y1 - 8))
	w, h = crop.size
	if w <= 0 or h <= 0:
		return 0.0

	px = crop.load()
	marked = sum(1 for y in range(0, h, 2) for x in range(0, w, 2) if px[x, y] < PAPER)
	return marked / max(1, (w / 2) * (h / 2))


# --- ocr ------------------------------------------------------------------------------------


def _tesseract(img, args: list[str], config: str | None = None) -> str:
	"""Run tesseract over one image and return stdout.

	Shelled out rather than wrapped, because the app may not add python dependencies -- see the
	module docstring. A missing binary is reported as the deployment problem it is instead of a
	traceback about `FileNotFoundError`.

	`config` is tesseract's output configfile ("tsv"), and it MUST come after the options. The
	CLI is `tesseract IMAGE OUTPUTBASE [options] [configfile...]`, and passing them the other way
	round does not error -- it silently ignores the options. That cost real time: every --psm
	value produced byte-identical output because none of them were being applied at all.
	"""
	import tempfile

	with tempfile.TemporaryDirectory() as tmp:
		path = os.path.join(tmp, "cell.png")
		img.save(path)
		try:
			done = subprocess.run(
				["tesseract", path, "stdout", *args, *([config] if config else [])],
				capture_output=True,
				text=True,
				timeout=120,
			)
		except FileNotFoundError:
			frappe.throw(
				_(
					"OCR is not available on this server: the tesseract package is missing. "
					"Rebuild the image, or attach the Word or Excel original instead of a scan."
				),
				title=_("OCR unavailable"),
			)
		except subprocess.TimeoutExpired:
			frappe.throw(_("Reading the attached document timed out."), title=_("Import failed"))

	return done.stdout or ""


def _page_words(img) -> list[dict]:
	"""Every word tesseract can see on the page, with its box -- ONE subprocess call.

	This is why the whole page is read at once rather than cell by cell. Per-cell OCR is more
	accurate (a tight crop and `--psm 7` beat a full page), but a 57-row sheet has ~340 populated
	cells and each call costs ~250ms of process startup: three minutes, inside a web request, to
	do what one call does in about two seconds. So the page is read once here, and only the cells
	that fail validation are re-read properly by `_repair`.
	"""
	tsv = _tesseract(img, ["--psm", PAGE_PSM], config="tsv")

	words = []
	for line in tsv.splitlines()[1:]:
		parts = line.split("\t")
		if len(parts) < 12:
			continue
		try:
			left, top, width, height = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
			conf = float(parts[10])
		except ValueError:
			continue
		text = parts[11].strip()
		if not text or conf < 0:
			continue
		words.append({
			"text": text,
			"cx": left + width / 2,
			"cy": top + height / 2,
			"conf": conf,
		})
	return words


# Characters tesseract produces from the ruled lines themselves rather than from anything
# written on the sheet. A cell whose content wraps ("TURKANA / WEST") comes back with the
# borders interleaved as "TURKANA _ | WEST", so these are stripped before the text is used.
BORDER_CHARS = "|_—–\\/!"


def _is_placeholder(text: str) -> bool:
	"""Whether a cell holds a dash standing in for "nothing" rather than a value.

	FoLT's own sheets use both: the printed attendance sheet renders an em dash for a location it
	does not have, and the reimbursement sheets put "-" in Sustenance and Accommodation on every
	row. Read literally, that becomes a location called "—".
	"""
	stripped = (text or "").strip()
	return bool(stripped) and not stripped.strip("-–—_.· ")


def _clean(text: str) -> str:
	"""Drop the ruled-line debris out of an OCR'd cell and collapse its whitespace."""
	tokens = []
	for token in (text or "").split():
		stripped = token.strip(BORDER_CHARS)
		# A token that was nothing BUT border characters is a line, not a word. One that merely
		# picked one up at an edge keeps its remainder.
		if stripped:
			tokens.append(stripped)
	return " ".join(tokens)


def _cell_text(words: list[dict], box) -> tuple[str, float]:
	"""Words whose centre falls inside a cell, in reading order, plus their mean confidence."""
	x0, y0, x1, y1 = box
	inside = [w for w in words if x0 < w["cx"] < x1 and y0 < w["cy"] < y1]
	if not inside:
		return "", 0.0

	inside.sort(key=lambda w: (round(w["cy"] / LINE_BUCKET), w["cx"]))
	text = _clean(" ".join(w["text"] for w in inside))
	return text, sum(w["conf"] for w in inside) / len(inside)


def _repair(img, box, *, digits: bool) -> str:
	"""Re-read one cell properly, after the cheap whole-page pass got it wrong.

	`--psm 7` treats the crop as a single line, and for the columns where a wrong character costs
	money the character set is restricted to digits outright. That whitelist is the single biggest
	accuracy win available here, and it is exactly what a whole-page pass cannot use, because the
	same pass has to read names.
	"""
	from PIL import Image

	x0, y0, x1, y1 = box
	crop = img.crop((x0 + CELL_INSET, y0 + CELL_INSET, x1 - CELL_INSET, y1 - CELL_INSET))
	if crop.width < 8 or crop.height < 8:
		return ""

	crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
	args = ["--psm", CELL_PSM]
	if digits:
		args += ["-c", "tessedit_char_whitelist=0123456789"]

	return " ".join(_tesseract(crop, args).split())


# --- turning a grid into rows ---------------------------------------------------------------


def map_columns(header: list[str]) -> dict[str, int]:
	"""Header cells -> column roles, by name rather than by position.

	Specific keys are consumed first (COLUMN_ORDER), because the synonyms overlap: "Phone
	Number" contains "number", and a sheet with both "Phone Number" and "ID Number" has to
	resolve them the right way round.
	"""
	lowered = [(i, (cell or "").lower().strip()) for i, cell in enumerate(header)]
	mapping: dict[str, int] = {}
	taken: set[int] = set()

	for role in COLUMN_ORDER:
		for keyword in COLUMN_SYNONYMS[role]:
			match = next((i for i, text in lowered if i not in taken and keyword in text), None)
			if match is not None:
				mapping[role] = match
				taken.add(match)
				break

	return mapping


def header_score(cells: list[str]) -> int:
	"""How many distinct column roles a band of cells looks like it names."""
	return len(map_columns(cells))


def _digits(text: str) -> str:
	return "".join(c for c in (text or "") if c.isdigit())


def _amount(text: str):
	"""A money cell as a number. '5,000/-' is 5000; '-' and '' are nothing at all.

	The dash matters: on the pack's sheets Sustenance and Accommodation are '-' on every row,
	and reading that as zero rather than as absent is what lets TOTAL still reconcile.
	"""
	digits = _digits(text)
	return int(digits) if digits else None


def _looks_handwritten(rows: list[dict]) -> bool:
	"""Whether the data rows are handwriting rather than print.

	The discriminator is the share of rows yielding a structurally valid Kenyan mobile number,
	because that is where the measured separation is absolute: 90.5% on the pack's typed sheets
	against 0% on its handwritten registers. Names are useless for this -- handwriting OCRs to
	confident-looking rubbish like 'Ekiesk Ka CHT E pus', which passes any is-this-a-name test.

	Only rows that have a phone column at all are counted. A sheet with no phone column cannot
	be judged this way and is let through to the normal review, flagged.
	"""
	with_phone = [r for r in rows if r.get("_phone_raw")]
	if len(with_phone) < 3:
		return False

	valid = sum(1 for r in with_phone if MOBILE_RE.match(r.get("mobile_number") or ""))
	return (valid / len(with_phone)) < READABLE_PHONE_SHARE


# --- the entry point ------------------------------------------------------------------------


def infer_columns(sample_rows: list[list[str]], mapping: dict[str, int], column_count: int) -> dict[str, int]:
	"""Fill in a name or phone column the header did not name, by looking at the data.

	Needed because a header cell is a short word in a shaded box and OCR drops those more often
	than it drops a row of a table -- FoLT's own printed sheet loses "Name" and "Mobile" while
	reading every attendee underneath perfectly. Without this the header is recognised, the two
	columns that matter are unmapped, and every row is silently skipped: the worst possible
	failure, because it looks like an empty sheet rather than a broken read.

	Inference is by content, not position, so it also rescues a field sheet whose columns are
	headed something nobody anticipated. A column is a phone column if most of its cells hold a
	9-to-12 digit run, and a name column if most hold letters and no long digit run.
	"""
	if not sample_rows:
		return mapping

	taken = set(mapping.values())
	scores: dict[str, list[tuple[float, int]]] = {"mobile_number": [], "participant_name": []}

	for col in range(column_count):
		if col in taken:
			continue
		cells = [row[col] for row in sample_rows if col < len(row) and (row[col] or "").strip()]
		if not cells:
			continue

		phoney = sum(1 for c in cells if 9 <= len(_digits(c)) <= 12) / len(cells)
		namey = sum(
			1 for c in cells
			if sum(ch.isalpha() for ch in c) >= 3 and len(_digits(c)) < 5
		) / len(cells)

		scores["mobile_number"].append((phoney, col))
		scores["participant_name"].append((namey, col))

	# Phone first: it is the more distinctive shape, so claiming it before the name column
	# prevents a column of numbers being taken as names on a sheet with no header at all.
	for role in ("mobile_number", "participant_name"):
		if role in mapping:
			continue
		candidates = [(score, col) for score, col in scores[role] if col not in taken and score >= 0.6]
		if candidates:
			candidates.sort(reverse=True)
			mapping[role] = candidates[0][1]
			taken.add(candidates[0][1])

	return mapping


def _rows_from_table(table: list[list[str]], warnings: list[str]) -> list[dict]:
	"""Rows out of an already-exact table (spreadsheet, or a PDF with a text layer)."""
	header_at = next(
		(i for i, row in enumerate(table[:10]) if header_score(row) >= HEADER_HITS_REQUIRED),
		None,
	)
	if header_at is None:
		return []

	mapping = map_columns(table[header_at])
	if "participant_name" not in mapping or "mobile_number" not in mapping:
		mapping = infer_columns(
			[r for r in table[header_at + 1: header_at + 9]],
			mapping,
			max(len(r) for r in table),
		)

	rows = []

	for raw in table[header_at + 1:]:
		# A repeated header is how a multi-page sheet joins, not a row about somebody.
		if header_score(raw) >= HEADER_HITS_REQUIRED:
			continue

		def cell(role):
			i = mapping.get(role)
			return (raw[i] if i is not None and i < len(raw) else "") or ""

		name = cell("participant_name").strip()
		phone_raw = cell("mobile_number")
		if not name and not _digits(phone_raw):
			continue

		rows.append(_build_row(name, phone_raw, cell, ink=None))

	if len(rows) > MAX_ROWS:
		warnings.append(_("Only the first {0} rows were read.").format(MAX_ROWS))
		rows = rows[:MAX_ROWS]

	return rows


def _build_row(name: str, phone_raw: str, cell, *, ink) -> dict:
	"""One parsed row, normalised but NOT validated -- validation belongs to the caller."""
	digits = _digits(phone_raw)
	# Accept the forms normalise_mobile accepts, so a sheet written +254... still lands as a
	# local number; anything else is left as read, for the review step to show and flag.
	if digits.startswith("254"):
		digits = "0" + digits[3:]
	elif len(digits) == 9 and digits[:1] in ("7", "1"):
		digits = "0" + digits

	total = _amount(cell("total"))
	components = {k: _amount(cell(k)) for k in ("transport", "sustenance", "accommodation")}

	location = cell("location")
	row = {
		"participant_name": " ".join(name.split()),
		"mobile_number": digits,
		"_phone_raw": phone_raw,
		"location": "" if _is_placeholder(location) else " ".join(location.split()),
		"category": _clean(cell("category")),
		"id_number": _digits(cell("id_number")) or "",
		"serial": _amount(cell("serial")),
		"total": total,
		**{k: v for k, v in components.items()},
	}

	gender = cell("gender").strip().upper()[:1]
	row["gender"] = {"F": "Female", "M": "Male"}.get(gender, "")

	if ink is None:
		ack = cell("acknowledgement").strip()
		row["acknowledgement"] = ack if ack in ("Signature", "Thumbprint", "None") else ("Signature" if ack else "")
		row["_ink"] = None
	else:
		# The mark itself decides, not a word: > 2% of the cell carrying ink is a signature or a
		# thumbprint, and an empty cell is somebody who did not sign.
		row["_ink"] = round(ink, 4)
		row["acknowledgement"] = "Signature" if ink > 0.02 else "None"

	return row


def parse(file_url: str) -> dict:
	"""Read a participant sheet.

	Returns `{"rows", "columns", "warnings", "source", "handwritten", "signed_marks", "pages"}`
	and writes nothing anywhere. `source` is "native" when the file carried real text and no OCR
	was needed, "ocr" otherwise.
	"""
	content, ext = _file_content(file_url)
	warnings: list[str] = []

	# 1. Exact paths first. If the Word/Excel original or its PDF export was attached, the sheet
	#    reads perfectly and none of the image machinery runs.
	tables: list[list[list[str]]] = []
	if ext in (".xlsx", ".xls", ".csv"):
		tables = _tables_from_spreadsheet(content, ext)
	elif ext == ".pdf":
		try:
			tables = _tables_from_text_pdf(content)
		except Exception as e:  # noqa: BLE001 - a broken text layer is not a reason to give up
			warnings.append(_("Could not read the PDF text layer ({0}); fell back to OCR.").format(type(e).__name__))

	for table in tables:
		rows = _rows_from_table(table, warnings)
		if rows:
			return {
				"rows": rows,
				"columns": map_columns(table[0]),
				"warnings": warnings,
				"source": "native",
				"handwritten": False,
				"signed_marks": sum(1 for r in rows if r.get("acknowledgement") in ("Signature", "Thumbprint")),
				"pages": 1,
			}

	if ext in (".xlsx", ".xls", ".csv"):
		frappe.throw(
			_("No attendee table was found in {0}. The sheet needs a header row naming at least a name and a phone column.").format(
				frappe.bold(file_url)
			),
			title=_("Nothing to import"),
		)

	# 2. OCR.
	if ext == ".pdf":
		pages = list(_pages_from_pdf(content))
	else:
		import io

		from PIL import Image

		try:
			pages = [Image.open(io.BytesIO(content)).convert("L")]
		except Exception:  # noqa: BLE001
			frappe.throw(
				_("{0} is not a document this can read. Attach a PDF, an image, or the Excel original.").format(
					frappe.bold(file_url)
				),
				title=_("Unsupported attachment"),
			)

	rows, columns, marks = [], {}, 0
	for index, page in enumerate(pages):
		page_rows, page_columns, page_marks = _read_page(page, warnings, index)
		rows += page_rows
		marks += page_marks
		columns = columns or page_columns

	if not rows:
		frappe.throw(
			_(
				"No attendee table could be found in the attached document. If this is a photo of a "
				"sheet, make sure the whole ruled table is in frame and square to the camera."
			),
			title=_("Nothing could be read"),
		)

	if len(rows) > MAX_ROWS:
		warnings.append(_("Only the first {0} rows were read.").format(MAX_ROWS))
		rows = rows[:MAX_ROWS]

	return {
		"rows": rows,
		"columns": columns,
		"warnings": warnings,
		"source": "ocr",
		"handwritten": _looks_handwritten(rows),
		"signed_marks": marks,
		"pages": len(pages),
	}


def _read_page(page, warnings: list[str], index: int) -> tuple[list[dict], dict, int]:
	"""OCR one page into rows. Returns (rows, column map, count of signature marks)."""
	page = _crop_to_paper(page)
	page, angle, peak = _deskew(page)

	# The rule threshold is relative to what the sharpest line on THIS page achieved. A fixed
	# fraction fails both ways: too high and a faint photocopy has no grid, too low and the
	# underlined title becomes a table rule.
	horizontals = _rules(page, axis=0, min_frac=max(RULE_FLOOR, peak * RULE_SHARE))
	verticals = _rules(page, axis=1, min_frac=0.30)

	if len(horizontals) < 3 or len(verticals) < 4:
		warnings.append(
			_("Page {0}: no ruled table found (skew {1}°). Skipped.").format(index + 1, angle)
		)
		return [], {}, 0

	words = _page_words(page)

	# Find the header band by what it says. The rules above the table -- the underline under the
	# activity title, on every FoLT sheet -- are indistinguishable from a table rule by ink
	# density alone, so position cannot be trusted.
	header_at, header_cells = None, []
	for r in range(len(horizontals) - 1):
		if horizontals[r + 1] - horizontals[r] < 20:
			continue
		cells = [
			_cell_text(words, (verticals[c], horizontals[r], verticals[c + 1], horizontals[r + 1]))[0]
			for c in range(len(verticals) - 1)
		]
		if header_score(cells) >= HEADER_HITS_REQUIRED:
			header_at, header_cells = r, cells
			break

	if header_at is None:
		warnings.append(
			_("Page {0}: found a table but no header row naming its columns. Skipped.").format(index + 1)
		)
		return [], {}, 0

	mapping = map_columns(header_cells)

	# What the header failed to name, the rows themselves can say. Sampled from the first few
	# data bands rather than all of them, because this only has to identify a column.
	if "participant_name" not in mapping or "mobile_number" not in mapping:
		sample = []
		for r in range(header_at + 1, min(header_at + 9, len(horizontals) - 1)):
			if horizontals[r + 1] - horizontals[r] < 22:
				continue
			sample.append([
				_cell_text(words, (verticals[c], horizontals[r], verticals[c + 1], horizontals[r + 1]))[0]
				for c in range(len(verticals) - 1)
			])
		before = set(mapping)
		mapping = infer_columns(sample, mapping, len(verticals) - 1)
		for role in set(mapping) - before:
			warnings.append(
				_("Page {0}: the {1} column was not named in the header; identified it from the rows.").format(
					index + 1, role.replace("_", " ")
				)
			)

	rows, marks, repairs = [], 0, 0

	for r in range(header_at + 1, len(horizontals) - 1):
		top, bottom = horizontals[r], horizontals[r + 1]
		if bottom - top < 22:
			continue

		# Past the bottom of the table. Everything below it -- the signoff underlines, the note --
		# is ruled too, and reading it produces attendees who are actually page furniture.
		if not _in_table(page, verticals, (top + bottom) // 2):
			break

		boxes = {
			role: (verticals[i], top, verticals[i + 1], bottom)
			for role, i in mapping.items()
			if i + 1 < len(verticals)
		}

		texts = {role: _cell_text(words, box) for role, box in boxes.items()}

		def cell(role):
			return texts.get(role, ("", 0.0))[0]

		name = cell("participant_name").strip()
		phone_raw = cell("mobile_number")
		if not name and not _digits(phone_raw):
			continue

		ink = _ink_share(page, boxes["acknowledgement"]) if "acknowledgement" in boxes else None
		row = _build_row(name, phone_raw, cell, ink=ink)

		# The repair pass. Only the two kinds of cell where the cheap read is demonstrably wrong
		# get re-read: a mobile that is not a mobile, and a total that does not equal its parts.
		# On the pack that is 2 cells in 21 rows, so it costs almost nothing -- and it is where
		# the digit whitelist earns its place.
		if repairs < MAX_REPAIRS and "mobile_number" in boxes and not MOBILE_RE.match(row["mobile_number"]):
			repairs += 1
			retry = _repair(page, boxes["mobile_number"], digits=True)
			if MOBILE_RE.match(_normalised(retry)):
				row["mobile_number"] = _normalised(retry)
				row["_repaired"] = True

		parts = sum(row.get(k) or 0 for k in ("transport", "sustenance", "accommodation"))
		if (
			repairs < MAX_REPAIRS
			and row.get("total") is not None
			and parts
			and row["total"] != parts
			and "total" in boxes
		):
			repairs += 1
			retry = _amount(_repair(page, boxes["total"], digits=True))
			if retry == parts:
				row["total"] = retry
				row["_repaired"] = True

		if row.get("_ink") and row["_ink"] > 0.02:
			marks += 1

		rows.append(row)

	if repairs:
		warnings.append(_("Page {0}: re-read {1} unreadable cell(s).").format(index + 1, repairs))

	return rows, mapping, marks


def _normalised(text: str) -> str:
	digits = _digits(text)
	if digits.startswith("254"):
		return "0" + digits[3:]
	if len(digits) == 9 and digits[:1] in ("7", "1"):
		return "0" + digits
	return digits

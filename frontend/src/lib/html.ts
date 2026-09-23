// Server messages are HTML, and they must stay HTML -- but safe HTML.
//
// FoLT states most of its rules in msgprints: "<b>REQ-2026-00012</b> is at Pending Head of
// Finance<br><br>Link either an approved <a href=…>Procurement Committee Evaluation</a> …". Escaped,
// that is gibberish; rendered raw, it is a hole, because the messages interpolate participant and
// supplier names and frappe.bold does not escape. So this keeps a short allowlist of inline tags,
// keeps links only when they point at this site or plainly at http(s), and drops everything else
// down to its text. DOMParser does the parsing, so no regex ever has to understand HTML.
//
// Desk links stay Desk links on purpose: rewriting /desk/... into SPA routes would need a
// doctype->route map that drifts the moment a doctype crosses it, and the failure would be a dead
// link inside an error message.

const INLINE = new Set(["B", "STRONG", "I", "EM", "BR", "A", "P", "UL", "OL", "LI", "SPAN", "CODE", "SMALL"]);

function safeHref(href: string | null): string | null {
  if (!href) return null;
  const value = href.trim();
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  if (/^https?:\/\//i.test(value)) return value;
  return null;
}

function clean(node: Node, into: Node) {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      into.appendChild(document.createTextNode(child.textContent ?? ""));
      continue;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) continue;
    const el = child as Element;
    if (!INLINE.has(el.tagName)) {
      // Unknown tags keep their words and lose their power: <script> and <style> lose both.
      if (el.tagName !== "SCRIPT" && el.tagName !== "STYLE") clean(el, into);
      continue;
    }
    const copy = document.createElement(el.tagName.toLowerCase());
    if (el.tagName === "A") {
      const href = safeHref(el.getAttribute("href"));
      if (href) {
        copy.setAttribute("href", href);
        // Server links point into the Desk; opening it in place would throw away an unsaved form.
        copy.setAttribute("target", "_blank");
        copy.setAttribute("rel", "noopener");
      }
    }
    clean(el, copy);
    into.appendChild(copy);
  }
}

export function sanitize(html: string | null | undefined): string {
  if (!html) return "";
  const parsed = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
  const out = document.createElement("div");
  clean(parsed.body.firstElementChild ?? parsed.body, out);
  return out.innerHTML;
}

/** The words only -- for aria labels and anywhere markup would be noise. */
export function plain(html: string | null | undefined): string {
  if (!html) return "";
  return new DOMParser().parseFromString(html, "text/html").body.textContent?.trim() ?? "";
}

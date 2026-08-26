__version__ = "0.0.1"

# Installed here, at app import, rather than from a hook -- because this is the only entry point
# that runs in *every* context. frappe imports `folt_customizations.hooks` (and so this package)
# before it can read a single hook, in web requests, background jobs, `bench execute`, the
# console and plain scripts alike. `before_request`/`before_job` would miss the last three, and
# `on_print_pdf` fires too late to help: `get_print` and `attach_print` each bind `get_pdf` as a
# local name at the top of their own body, so by the time that hook runs the function to be
# wrapped has already been looked up. See print_formats.guard_pdf_host for what it guards
# against and why.
from folt_customizations.print_formats import guard_pdf_host

guard_pdf_host()

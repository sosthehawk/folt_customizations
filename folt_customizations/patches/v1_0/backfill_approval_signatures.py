"""Name the approvers of documents moved before approval signatures were recorded.

Rebuilt from each document's Workflow comments; no signature is invented. See
approval_signatures.backfill.
"""

from folt_customizations.approval_signatures import backfill


def execute():
	backfill()

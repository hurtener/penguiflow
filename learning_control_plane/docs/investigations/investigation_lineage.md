# Investigation digest lineage

Milestone 16 links every governance record to the immutable investigation
documents that supplied its evidence. A digest is a content identity, not a copy
of the document.

```text
investigation digest
  -> mining record / held-out evaluation case
  -> candidate and gate decision
  -> human review
  -> delivery authorization
  -> provider activation receipt
```

Candidate mining retains the digests of earlier successful investigations.
Held-out evaluation cases retain the digests of later investigations. When the
gate runs, it creates one ordered, duplicate-free evidence list from both sets.
That list is copied unchanged into the review, authorization, and receipt.

The digest fields do not change candidate drafting, paired evaluation, gate
thresholds, review policy, or delivery permissions. They make the existing
workflow auditable: a receipt can be traced back to the exact investigation
documents considered before delivery.

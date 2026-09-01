# Sample output

Captured verbatim from the two demo scripts. Nothing here is hand-edited except
one elided chunk body, marked inline.

## `python scripts/demo_live.py`

A real MCP client, a server in a separate process, and a corpus that changes
mid-session. The timings are measured, not staged.

```
========================================================================
WarnSync live protocol demo
corpus: /tmp/warnsync-live-XXXXXX/corpus
poll interval: 2.0s
========================================================================

1. Connected
  server: warnsync v0.1.0
  protocol: 2026-07-28

2. Tool surface (stable — synchronization never changes it)
  search_letters: Hybrid search over the current corpus. Returns the top-k matching chunks, each with the provenance needed to cite it.
  get_letter: Fetch one letter's full text and metadata at a specific version.
  violation_trends: Count cited provisions across the corpus in a date window, with the most-cited recipients per provision.
  list_updates: Enumerate committed corpus changes since a timestamp — the refetch half of the notify-then-fetch pattern.
  corpus_status: Freshness and synchronization state: corpus size, retained versions, last commit, and last-pass ingestion cost.

3. Query before the letter exists
  WL-2026-0119 v1 c0 (0.139) — Halcyon Contract Manufacturing, Ltd.
  WL-2025-0412 v1 c0 (0.109) — Northgate Sterile Solutions, Inc.
  no Stonebrook letter in the corpus yet — correct, it has not been posted
  corpus: 5 active letters, 9 chunks

4. Subscribing to warnsync://manifest
  subscription acknowledged; the server will push on every commit

5. A new letter appears upstream
  dropped WL-2026-0224.json into the watched corpus at 18:35:32
  (the server is polling; nothing was told to it directly)

6. Waiting for the protocol-native notification...
  + 1.96s  ResourceUpdated  uri=warnsync://manifest
  + 1.96s  ResourcesListChanged

7. Refetching what changed (notify-then-fetch)
  NEW      WL-2026-0224 v1 at 2026-08-31T23:35:34Z
  manifest now lists 6 letters

8. The same query, after the commit
  WL-2026-0224 v1 c0 (0.445) — Stonebrook Biologics, Inc.
  WL-2026-0119 v1 c0 (0.139) — Halcyon Contract Manufacturing, Ltd.

9. Provenance for the fresh answer
  recipient: Stonebrook Biologics, Inc.
  office: Center for Biologics Evaluation and Research (CBER)
  issuance_date: 2026-02-24
  posting_date: 2026-03-04
  cfr_citations: ['21 CFR 211.192', '21 CFR 600.14', '21 CFR 211.113(b)', '21 CFR 211.42(c)(10)(iv)', '21 CFR 600.12(b)']
  ingested_at: 2026-08-31T23:35:34Z
  cite_as: WL-2026-0224 v1 chunk 0

10. Freshness lag
  posted (file appeared)     +0.00s
  notification delivered     +1.96s
  poll interval was          2.00s (the lower bound on detection)
  the query path was never blocked: step 3's query was served throughout

Done.
```

## `python scripts/demo.py`

The same machinery in-process, printing the payload of every tool call and
walking through revision, arrival, withdrawal and point-in-time reads.

```
========================================================================
WarnSync reference implementation — corpus: /tmp/warnsync-demo-XXXXXX/corpus
========================================================================

Pass 1 — cold start (every letter is NEW)
-----------------------------------------
{
  "events": [
    {
      "letter_id": "WL-2025-0412",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2025-0518",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2025-0603",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2025-0721",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2026-0119",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    }
  ],
  "sync_stats": {
    "passes": 1,
    "letters_seen": 5,
    "unchanged": 0,
    "new": 5,
    "modified": 0,
    "removed": 0,
    "chunks_embedded": 9,
    "reprocessed_fraction": 1.0,
    "last_pass_seconds": 0.0048
  }
}

Pass 2 — idempotence (identical fingerprints, nothing re-embedded)
------------------------------------------------------------------
{
  "events_emitted": 0,
  "sync_stats": {
    "passes": 2,
    "letters_seen": 5,
    "unchanged": 5,
    "new": 0,
    "modified": 0,
    "removed": 0,
    "chunks_embedded": 9,
    "reprocessed_fraction": 0.0,
    "last_pass_seconds": 0.0013
  }
}

search_letters("out-of-specification investigation root cause", k=3)
--------------------------------------------------------------------
  0.455  WL-2026-0119 v1 c0  Halcyon Contract Manufacturing, Ltd.
         The U.S. Food and Drug Administration inspected your drug manufacturing facility from October 6 ...
  0.427  WL-2025-0412 v1 c0  Northgate Sterile Solutions, Inc.
         The U.S. Food and Drug Administration inspected your drug manufacturing facility from January 13...
  0.122  WL-2025-0412 v1 c1  Northgate Sterile Solutions, Inc.
         5. Your firm failed to establish and follow appropriate written procedures designed to prevent m...

search_letters("audit trail deleted chromatography data", k=2)
--------------------------------------------------------------
  0.487  WL-2025-0518 v1 c0  Cascade Analytical Laboratories LLC
         The U.S. Food and Drug Administration inspected your contract testing laboratory from February 2...
  0.106  WL-2025-0518 v1 c1  Cascade Analytical Laboratories LLC
         Quality unit oversight. The quality unit at your firm reports to the same executive who owns com...

search_letters(... k=1) — a single result with full provenance
--------------------------------------------------------------
{
  "letter_id": "WL-2025-0518",
  "version": 1,
  "chunk_id": 0,
  "score": 0.3181,
  "text": "[SYNTHETIC EXAMPLE — fictional letter written for the WarnSync reference implementation. Not a real FDA record.]\n\nDear Dr. Whitfield:\n\nThe U.S. Food and Drug Administration inspected your contract testing laboratory from February 24 to March 6, 2025. Our investigators observed significant deviations from current good manufacturing practice for finished pharmaceuticals, 21 CFR parts 210 and 211. Your firm performs release and stability testing on behalf of drug manufacturers; the deficiencies below call into question the reliability of the data you generate for your clients.\n\n1. Your firm failed to exercise appropriate controls over computer or related systems to assure that only authorized personnel institute changes in master production and control records, or other records (21 CFR 211.68(b)). All analysts in your chromatography laboratory shared a single Windows account with administrator privileges. Audit trails on four of six high performance liquid chromatography workstations were disabled at the time of the inspection, and your firm could not determine when they had been disabled or by whom.\n\n2. Your firm failed to ensure that laboratory records included complete data derived from all tests necessary to assure compliance with established specifications and standards (21 CFR 211.194(a)). Our investigators recovered 268 chromatographic injections in folders named \"trial\" and \"demo\" that were not reported in the corresponding analytical records. Fourteen of these injections were for assay testing of client batches that were subsequently released.\n\n3. Your firm failed to prevent unauthorized deletion of electronic raw data. During the inspection, an analyst deleted a sequence of standard injections after being asked to produce the audit trail for a stability sample. Your quality unit did not have a procedure requiring review of audit trails as part of analytical data review.\n\nData integrity remediation. Your response should include a current risk assessment of the effects of the observed failures on the quality of the data you have supplied to each client, a management strategy that includes a detailed corrective action and preventive action plan, and the report of a qualified third party evaluating the extent of the data integrity deficiencies.\n\nWe remind you that your clients remain responsible under 21 CFR 211.22(a) for the quality of the drug products they release using your data.\n\n4. Your firm failed to establish and follow an adequa

violation_trends(provision="21 CFR 211")
----------------------------------------
{
  "provision_filter": "21 CFR 211",
  "window": {
    "start": null,
    "end": null
  },
  "letters_in_window": 5,
  "letters_matching": 3,
  "provisions": [
    {
      "provision": "21 CFR 211.22(a)",
      "letters_citing": 3,
      "top_recipients": [
        {
          "recipient": "Cascade Analytical Laboratories LLC",
          "letters": 1
        },
        {
          "recipient": "Halcyon Contract Manufacturing, Ltd.",
          "letters": 1
        },
        {
          "recipient": "Northgate Sterile Solutions, Inc.",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.192",
      "letters_citing": 2,
      "top_recipients": [
        {
          "recipient": "Halcyon Contract Manufacturing, Ltd.",
          "letters": 1
        },
        {
          "recipient": "Northgate Sterile Solutions, Inc.",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.100(a)",
      "letters_citing": 1,
      "top_recipients": [
        {
          "recipient": "Halcyon Contract Manufacturing, Ltd.",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.113(b)",
      "letters_citing": 1,
      "top_recipients": [
        {
          "recipient": "Northgate Sterile Solutions, Inc.",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.160(b)",
      "letters_citing": 1,
      "top_recipients": [
        {
          "recipient": "Northgate Sterile Solutions, Inc.",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.166(a)",
      "letters_citing": 1,
      "top_recipients": [
        {
          "recipient": "Cascade Analytical Laboratories LLC",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.194(a)",
      "letters_citing": 1,
      "top_recipients": [
        {
          "recipient": "Cascade Analytical Laboratories LLC",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.194(a)(4)",
      "letters_citing": 1,
      "top_recipients": [
        {
          "recipient": "Cascade Analytical Laboratories LLC",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.42(c)(10)(iv)",
      "letters_citing": 1,
      "top_recipients": [
        {
          "recipient": "Northgate Sterile Solutions, Inc.",
          "letters": 1
        }
      ]
    },
    {
      "provision": "21 CFR 211.67(a)",
      "letters_citing": 1,
      "top_recipients": [
        {
          "recipient": "Northgate Sterile S

get_letter('WL-2025-0412') — metadata (text elided)
---------------------------------------------------
{
  "letter_id": "WL-2025-0412",
  "version": 1,
  "issuance_date": "2025-04-12",
  "posting_date": "2025-04-22",
  "ingested_at": "2026-08-31T23:35:34Z",
  "recipient": "Northgate Sterile Solutions, Inc.",
  "office": "Center for Drug Evaluation and Research (CDER)",
  "subject": "Warning Letter — CGMP violations, sterile drug products",
  "cfr_citations": [
    "21 CFR 211.192",
    "21 CFR 211.42(c)(10)(iv)",
    "21 CFR 211.160(b)",
    "21 CFR 211.22(a)",
    "21 CFR 211.113(b)",
    "21 CFR 211.67(a)",
    "FD&C Act § 501(a)(2)(B)"
  ],
  "status": "active",
  "fingerprint": "46e1d0a74848042efae1ebc829726f302be41db4129a3f37058776db1ac0f4ec",
  "chunks": 2,
  "synthetic": true
}

Pass 3 — one letter revised upstream (MODIFIED, only that letter re-embedded)
-----------------------------------------------------------------------------
{
  "events": [
    {
      "letter_id": "WL-2026-0119",
      "version": 2,
      "kind": "MODIFIED",
      "committed_at": "2026-08-31T23:35:34Z"
    }
  ],
  "sync_stats": {
    "passes": 3,
    "letters_seen": 5,
    "unchanged": 4,
    "new": 0,
    "modified": 1,
    "removed": 0,
    "chunks_embedded": 11,
    "reprocessed_fraction": 0.2,
    "last_pass_seconds": 0.0021
  }
}

Both versions remain readable — an already-cited answer stays reproducible
--------------------------------------------------------------------------
{
  "v1": {
    "fingerprint": "8e452d57fbdcb374",
    "chunks": 1,
    "citations": [
      "21 CFR 211.192",
      "21 CFR 211.100(a)",
      "21 CFR 211.67(b)",
      "21 CFR 211.22(a)"
    ],
    "ingested_at": "2026-08-31T23:35:34Z"
  },
  "v2": {
    "fingerprint": "cff8c39ec0b1a34b",
    "chunks": 2,
    "citations": [
      "21 CFR 211.192",
      "21 CFR 211.100(a)",
      "21 CFR 211.67(b)",
      "21 CFR 211.22(a)",
      "21 CFR 211.166(a)"
    ],
    "ingested_at": "2026-08-31T23:35:34Z"
  },
  "current_alias": 2
}

Pass 4 — a new letter is posted (NEW)
-------------------------------------
{
  "events": [
    {
      "letter_id": "WL-2026-0224",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    }
  ],
  "sync_stats": {
    "passes": 4,
    "letters_seen": 6,
    "unchanged": 5,
    "new": 1,
    "modified": 0,
    "removed": 0,
    "chunks_embedded": 13,
    "reprocessed_fraction": 0.1667,
    "last_pass_seconds": 0.002
  }
}

The new letter is queryable immediately: search_letters("bioburden excursion reporting")
----------------------------------------------------------------------------------------
  0.445  WL-2026-0224 v1 c0  Stonebrook Biologics, Inc.
         The U.S. Food and Drug Administration inspected your licensed biologics manufacturing facility f...
  0.139  WL-2026-0119 v2 c0  Halcyon Contract Manufacturing, Ltd.
         The U.S. Food and Drug Administration inspected your drug manufacturing facility from October 6 ...

Pass 5 — a letter disappears upstream (REMOVED, recorded as a version)
----------------------------------------------------------------------
{
  "events": [
    {
      "letter_id": "WL-2025-0721",
      "version": 2,
      "kind": "REMOVED",
      "committed_at": "2026-08-31T23:35:34Z"
    }
  ],
  "withdrawn_letter": {
    "letter_id": "WL-2025-0721",
    "version": 2,
    "issuance_date": "2025-07-21",
    "posting_date": "2025-07-30",
    "ingested_at": "2026-08-31T23:35:34Z",
    "recipient": "Bluefin Nutraceuticals Corp.",
    "office": "Center for Food Safety and Applied Nutrition (CFSAN)",
    "subject": "Warning Letter — dietary supplement CGMP and labeling violations",
    "cfr_citations": [
      "21 CFR 111",
      "21 CFR 111.70(e)",
      "21 CFR 111.75(a)(1)(i)",
      "21 CFR 111.103",
      "21 CFR 111.255(b)",
      "FD&C Act § 201(g)(1)(B)",
      "FD&C Act § 403(a)(1)",
      "FD&C Act § 201(p)"
    ],
    "status": "withdrawn",
    "fingerprint": "a407be1e679ace7ae73abae007ad33f83e48db3d7f62656b4489e8af5e90cf1f",
    "chunks": 2,
    "synthetic": true
  },
  "still_readable_at_v1": "active",
  "excluded_from_search": true
}

Point-in-time read — the corpus as it stood before the revision
---------------------------------------------------------------
{
  "as_of": "2026-08-31T23:35:34Z",
  "letters_then": [
    "WL-2025-0412 v1",
    "WL-2025-0518 v1",
    "WL-2025-0603 v1",
    "WL-2025-0721 v1",
    "WL-2026-0119 v1"
  ],
  "letters_now": [
    "WL-2025-0412 v1",
    "WL-2025-0518 v1",
    "WL-2025-0603 v1",
    "WL-2026-0119 v2",
    "WL-2026-0224 v1"
  ]
}

list_updates(since=start) — the full update log
-----------------------------------------------
{
  "updates": [
    {
      "letter_id": "WL-2025-0412",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2025-0518",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2025-0603",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2025-0721",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2026-0119",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2026-0119",
      "version": 2,
      "kind": "MODIFIED",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2026-0224",
      "version": 1,
      "kind": "NEW",
      "committed_at": "2026-08-31T23:35:34Z"
    },
    {
      "letter_id": "WL-2025-0721",
      "version": 2,
      "kind": "REMOVED",
      "committed_at": "2026-08-31T23:35:34Z"
    }
  ]
}

corpus_status()
---------------
{
  "store": {
    "letters": 6,
    "active_letters": 5,
    "withdrawn_letters": 1,
    "versions_retained": 8,
    "tombstoned_versions": 2,
    "chunks_current": 12,
    "committed_changes": 8,
    "last_commit": "2026-08-31T23:35:34Z"
  },
  "sync": {
    "passes": 5,
    "letters_seen": 5,
    "unchanged": 5,
    "new": 0,
    "modified": 0,
    "removed": 1,
    "chunks_embedded": 13,
    "reprocessed_fraction": 0.0,
    "last_pass_seconds": 0.0012
  }
}

Done.
```

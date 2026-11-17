# Ghana field data: what must be documented before it can be used

This page sets out what the project must have on record before any Ghanaian field data
(manifest category `ghana_field`) can be approved, split into training, validation or test
data, or used for any AI purpose, and how the dataset tools enforce it. **No Ghanaian field
data has been collected or read for this milestone, and this page does not collect any.** It
describes requirements for collection that has not yet happened.

It is a statement of what the tools check, not legal or ethical advice. The permissions,
consent arrangements and privacy rules that apply to a particular collection are decided by the
project owner and the appropriate authorities (the land or site owner, any institution
involved, and whatever law and research-ethics process applies). The tools record what the
project says it has obtained; they cannot check that it is true.

## The rule

A `ghana_field` dataset **fails closed**. Until its collection permission, consent and
location privacy are documented and reviewed, it cannot be approved (`permissions.status`
`approved`), cannot list any permitted use, cannot have any record assigned to a split, and
cannot be cleared for any use. A missing or unresolved safeguard is reported exactly as it is:
the validator never fills in a gap, never assumes permission or consent, and never defaults a
privacy decision. The dataset can still be described while the gaps are open: a manifest that is
`pending_review`, lists no permitted uses and leaves every record `unassigned` is accepted with a
`field-governance-unresolved` warning, and nothing can be cleared for it.

## What the manifest must record

A `ghana_field` manifest carries a `dataset.field_governance` record
([schema](dataset_manifest_schema_1_1.md)). It is refused on any other category.

| Part | Resolved when | Otherwise |
|---|---|---|
| **Field-collection permission** (`collection_permission`) | `status` is `documented`, and `granted_by` (who gave it), `reference` (the letter, email or agreement it is recorded in) and `recorded_on` (its date) are all given | `pending`, `not_obtained` and `unknown` are unresolved, as is a `documented` permission missing any of the three |
| **Consent**, where it applies (`consent`) | `status` is `obtained` or `not_applicable`, and `basis` says who consented, or why consent does not apply (for example that no people or private property appear) | `pending`, `not_obtained` and `unknown` are unresolved, as is a resolved status with no `basis` |
| **Location privacy and handling** (`location_privacy`) | `handling` is `removed` (location data deleted), `generalised` with the `precision` that is kept stated, or `exact_restricted` with `access_restriction` saying how access is limited | `unresolved`, or a `generalised` or `exact_restricted` handling without its explanation |
| **Responsible reviewer and review date** (`reviewed_by`, `reviewed_on`) | both given, the date not in the future, and not earlier than the permission it relied on | missing either |
| **Permitted uses** (`permitted_uses`) | at least one of `training`, `validation`, `testing`, `independent_evaluation` | none listed |

`dataset.permissions.permitted_uses` may not allow a use that `field_governance.permitted_uses`
does not (`use-exceeds-field-permission`): the licence-style permission and the field permission
and consent both have to cover a use.

**A placeholder is not a record.** Every text value above must actually say something. Whitespace
or invisible characters only, a placeholder word (`x`, `n/a`, `none`, `unknown`, `yes`, `no`, `ok`,
`TBD`, `pending`, `see above`, `test`, `placeholder`), a value made of one repeated character, and a
value that is too short to name anything are treated as not recorded, so the safeguard stays
unresolved. A name or reference (`granted_by`, `reference`, `reviewed_by`, `precision`) needs at
least 3 letters or digits, so short real values such as `Ama Boateng`, `PER-7` or `50 m` are
accepted; an explanation (`consent.basis`, `location_privacy.access_restriction`, `notes`) needs at
least 8 and must be more than a placeholder. Non-Latin scripts are judged the same way.

These are plausibility checks on the text and no more: a value that passes can still be wrong or
invented, and the tool does not verify any of it.

## Requirements for future field collection

These are the things a future collection must settle and write down *before* its photographs are
used, so the manifest can honestly say they are resolved.

1. **Permission to collect.** Obtain, and keep a reference to, the permission of whoever controls
   each site. Record who granted it, in what document, and when.
2. **Consent where people or private property are involved.** Decide whether consent applies. If
   it does, obtain it before collection and record its basis and a reference. If it does not,
   write down why. Do not guess: `unknown` is an answer that blocks use.
3. **Location privacy.** Decide before collection how location will be handled: not recorded,
   deleted from the photographs, kept only at a coarse stated precision, or kept exactly under a
   stated access restriction. Photograph files can carry location (EXIF GPS) and the dataset tools
   do not read image contents, so this decision has to be carried out by the people collecting and
   preparing the data, and recorded here.
4. **A named reviewer and a date.** Someone responsible reviews the above and signs it off.
5. **Permitted uses.** State which uses the permission and consent actually cover. A use that is
   not listed is not permitted.
6. **A collection protocol.** The project's data strategy
   ([`dataset_strategy.md`](dataset_strategy.md)) requires a documented collection protocol for
   field data; it is the place for the detail (what is photographed, how, by whom, how physical
   samples are identified, how laboratory results will be tied to them).

The manifest records pointers to these documents. Keep the documents themselves, and any personal
or location data, out of Git.

## What the tools do, and do not do

- Validation, the split generator and the audit apply the rule above. An approved or split
  `ghana_field` dataset with a gap is an error (`field-governance-unresolved`); a draft with gaps is
  a warning; a resolved record lets the dataset be treated like any other, subject to every other
  check (licence evidence, restricted material, files and checksums, sample identity).
- They do **not** verify that a permission exists, that consent was genuine, that location data
  was really removed, or that a reviewer reviewed anything. A resolved record is a statement by the
  project, not evidence the tools have seen.
- They do not inspect photographs for location data or for people.
- Ghana field data is not UMaT/MN273 material and is not refused as such. But no field data may
  be mixed with restricted material, and a manifest that mentions UMaT or MN273 is refused whatever
  its category (see [`dataset_workflow.md`](dataset_workflow.md)).

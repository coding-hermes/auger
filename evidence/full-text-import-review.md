# Full-text import review

A source-to-render comparison found that documents classified as provenance-only
were not included in the text-parity comparison. A coverage manifest alone does
not establish that their contents can be retrieved.

Before marking full-document coverage complete:

- Reconcile the intended scope with the existing provenance-only classification.
- Check source-to-retrievable-text parity for every in-scope document, including
  duplicates and attachment-classified documents, and test a deliberate mutation.
- Register the corpus in an isolated namespace, read the registered entries back,
  and run an audit-inclusive retrieval battery. Report misses separately from any
  historical baseline.
- Review verification obligations by method; require executable bindings where
  appropriate without treating every obligation as a runnable test.

The source documents and operational evidence are not part of this public review.
This summary is not proof that registration, retrieval, or full coverage passed.

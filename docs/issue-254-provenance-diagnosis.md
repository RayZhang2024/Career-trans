# Issue #254: CV provenance diagnosis

## Trace through the current implementation

1. `CVFileExtractionService.extract` hashes the uploaded bytes with SHA-256.
   It creates `ExtractedCVSegment` records whose IDs are `<document SHA-256>:<section index + 1>` (for PDFs, the index is the page index). The IDs are written into both each segment's `segment_id` and the document's `provenance.segment_ids`, then persisted in `documents_json`. The persisted document objects are loaded once for an interpretation request and reused for generation and validation.
2. `CVIngestionService.interpret` passes the unstructured `ExtractedCVDocument` objects directly to `SemanticCVInterpreter`. That interpreter serializes their complete `model_dump(mode="json")` values. The provider receives both `documents[].segments[].segment_id` and `documents[].provenance.segment_ids`, without renaming, indexing, or truncating either list.
3. The CV extraction prompt currently says not to cite a document or segment absent from the input. It does not require exact copying of the canonical identifier, show the `<sha>:<index>` format, or distinguish IDs from aliases such as section numbers.
4. `EvidenceProvenance.document_sha256` and every item in `segment_ids` are plain strings in the Pydantic/strict output schema. Schema generation therefore permits arbitrary strings at the provider boundary.
5. The response is JSON-decoded and validated as `CandidateCVData`. This checks shape and types, not source membership. No response normalization rewrites segment IDs. `CVMergeService` deduplicates evidence while unioning its existing provenance records; it does not transform IDs.
6. Before merge or persistence, `_enrich_provenance` builds the allowed set from the same request documents' `provenance.segment_ids`. It separately requires a known document SHA, at least one segment ID, and that every ID be a member of that document's set. Any invalid evidence raises `ValueError`; the interpret route returns HTTP 422. Since validation occurs before the transaction commits, the draft remains `uploaded` and no review baseline or canonical evidence is created.

## Benchmark evidence and classification

The supplied benchmark database contains one uploaded `02_master_cv.md` draft. Its document SHA is `57e343ff303a813c74853b14c8980a26b6ce87fce8eb6267f823ffce953feab8`, and its canonical segment IDs are that SHA followed by `:1` through `:11`. The persisted segment objects and `provenance.segment_ids` match exactly. The database contains no provider request, response, or rejected ID, so the precise string returned during the reported run cannot be recovered from this reproduction data.

The reported proximate event is category A: the provider emitted provenance outside the valid source set. The code rules out C, D, E, and F for this path: no aliases are intentionally substituted, the same persisted objects are used at generation and validation, post-processing does not rewrite the IDs, and the document SHA/segment set matches internally. A contributing contract defect (category B) is directly observable: the prompt is not explicit about exact canonical IDs, and the generated schema leaves both document and segment identifiers unconstrained strings. That allows an alias or hallucinated ID to reach the deterministic validator, which correctly rejects it. The original provider response was not persisted, so claiming its exact invalid ID or distinguishing an alias from another hallucination would be speculation.

## Fix direction

Constrain provider structured output to the exact document SHA values and segment IDs in the current request, and state the exact-copy requirement in the prompt. Keep `_enrich_provenance` unchanged as the final authority; it still rejects wrong document/segment pairings and any response outside the active source set. Cover the request/schema parity and the failure with a fixed benchmark-style Markdown/provider fixture, without a live provider dependency.

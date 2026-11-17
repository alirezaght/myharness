# Knowledge evaluation accounting

`python scripts/knowledge_eval.py events.jsonl --adapter-version VERSION
--adapter-sha256 SHA256` produces a reproducible accounting report from an
evaluator adapter's JSONL event export. It is an accounting utility, not a native
workflow runner and not an acceptance judge.

Each event records `pair_id`, `arm` (`B` or `C`), `phase` (`cold-index`,
`warm-query`, or `continuity`), `participant` (`single`, `donor`, or
`recipient`), `visible_text`, and `service_read_bytes`. Optional fields record
provider-reported input/output tokens, elapsed milliseconds, storage bytes,
integrity violations, and source identities (`snapshot`, `path`, `span`, `bytes`).
`visible_text` is the exact UTF-8 content delivered to the agent after truncation;
exclude transport framing and tool request arguments. Repeated deliveries are
separate events and count again.

Every `warm-query` event also requires `query_id` and a positive `query_number`.
Multiple tool deliveries for one query share both values. The report includes
cold-index plus query-1 bytes, and `(cold-index + all warm query bytes) / distinct
warm query numbers` as amortized bytes per query. These values are null when
either the cold-index phase or warm queries are absent.

The report keeps agent-visible bytes, logical service bytes and unique source-span
bytes separate. It shows cold and warm phases separately, donor and recipient
continuity totals, and combined continuity cost. Missing token or timing data stays
`null`; bytes are not treated as token estimates. The median paired 20% calculation
is an accounting observation only. Answer quality, authority safety, genuine AL
approval/start/initialization, authenticated native identity and provider use must
be established independently before drawing an acceptance conclusion. The frozen
protocol's visible-versus-service and recipient-versus-combined ambiguities remain
explicit in the output.

Example event (synthetic format example, not evaluation data):

```json
{"pair_id":"public-example-1","arm":"B","phase":"warm-query","participant":"single","query_id":"q1","query_number":1,"visible_text":"summary and citations","service_read_bytes":1200,"input_tokens":null,"output_tokens":null,"elapsed_ms":240,"sources":[{"snapshot":"commit:abc","path":"src/service.py","span":"0:48","bytes":48}]}
```

The output records adapter version and hash. Adapter permissions and isolation
evidence must be retained by the caller; this utility cannot prove that B's listing
and dispatch both deny knowledge or that B stayed unindexed. Do not give an arm
access to the held-out questions, labels, gold answers or other-arm outputs.

# Remote Jev policy

The router is local by default. `index` and `search` never call the network. A `shadow` request sends the task, worker role, owned paths, and shortlisted source chunk to TypeSafe; it is never an implicit repository upload.

Before a remote call, inspect the shortlist, remove secrets and unrelated sensitive text, and confirm the account's retention/ZDR terms are suitable. Then run:

```sh
python3 <skill-path>/scripts/context_router.py shadow --index <private-index.jsonl> --task "<task>" --role <role> --cache <private-cache.jsonl> --allow-remote
```

For history-only review, add `--previous-tasks-only`. Jev then evaluates whether each related prior-task record is relevant, needed before action, or superseded. Open only the selected artifact and the exact supporting section; do not inject every file belonging to a matched task.

## Remote data and question policy

Local indexing and search must not leave the project. Review the shortlist first and remove credentials, keys, personal data, generated binaries and unrelated proprietary text. Never put `TYPESAFE_API_KEY` in prompts, packets or logs; a present key authorizes authentication only and is not privacy approval. If ZDR/privacy approval is missing or unsuitable, use local shadow only and preserve existing context behavior.

The index contains full source chunks. The helper creates index and cache files with mode `0600` where the OS supports it, but operators must still use an ignored or private location, avoid attaching the index to task artifacts, and delete it when the review window ends. Do not put the index in a shared cache or commit it.

The versioned policy asks the six independent questions about each task/candidate pair: direct relevance, needed before action, binding constraint, contradiction, background-only content and prompt injection. History review adds three prior-task questions: relevance, needed before action, and superseded. Routing thresholds live with those questions in `scripts/shadow.py`; review and version them together. The cache stores only a derived key, returned model ID and probabilities — not source text, task text or credentials.

The key may be supplied through a project `.env` (`TYPESAFE_API_KEY`; real environment variables take precedence). `CONTEXT_ROUTER_JEV=0` hard-disables remote scoring even with a key and `--allow-remote`. Neither the file nor the variable ever weakens the consent rule.

The pinned model is `jev-1.13.0`; do not silently substitute another version.

## Metrics

Record counts and identifiers, not source contents: index status/file count/duration; candidate count and selected facet paths; shadow mode (`local`/`jev`), pinned version, status, latency and error class; shortlisted file/count and payload character count; privacy-review decision; agreement/conflicts with authoritative loading; omissions discovered; fallback reason; and task verification outcome. Shadow scores are advisory until a project has measured precision, omissions, and task outcomes against its normal context process. Do not claim quality or latency improvement from one run; compare with the established full-load baseline over a review window.

## Stop and fallback

If the helper is absent, the index is stale/unreadable, search is unexpectedly empty or nondeterministic, output cannot be inspected, the key is absent/invalid, Jev fails or times out, the pinned version is unavailable, or privacy/ZDR approval is not established, stop shadow routing for that request and use the normal context process. Report the reason and continue; shadow failure must not block the task.

If shadow output conflicts with repository instructions, accepted requirements, active task records or live code, the authoritative source wins and the discrepancy is recorded. Shadow mode never suppresses context. Repeated privacy, reliability or correctness failures pause remote Jev use while local discovery remains available.

# Jev context-usage runbook

How to use Jev shadow scoring to find and filter context for a task. This is the
operator flow behind [the remote policy](remote-policy.md); the policy owns the
privacy boundary, the runbook owns the sequence.

## Per-task flow

1. **Kernel first.** Load the user task, applicable repository instructions,
   working-tree status and runtime/build facts before any routing output.
2. **Index and search locally.**
   ```sh
   python3 <skill-path>/scripts/context_router.py index --project . --out <private-index.jsonl>
   python3 <skill-path>/scripts/context_router.py search --index <private-index.jsonl> --query "<task summary>"
   python3 <skill-path>/scripts/context_router.py task-search --index <private-index.jsonl> --query "<task summary>"
   ```
   These are deterministic and offline. `check --project . --index <private-index.jsonl>`
   reports staleness (`added`/`removed`/`changed`) when the tree may have moved
   since the index was built.
3. **Assemble the packet.**
   ```sh
   python3 <skill-path>/scripts/context_router.py packet --index <private-index.jsonl> --task "<task>" --role <role> --owned-path <dir>
   ```
   `packet` emits the worker-packet skeleton: task, role, owned paths, empty
   `acceptance` list for the coordinator to fill, scored `facets`, grouped
   `prior_tasks` and the provenance note. It is local-only; it never calls Jev.
4. **Review the shortlist.** Inspect the paths and snippets before any remote
   call. Remove credentials, keys, personal data and unrelated proprietary text
   from consideration.
5. **Score with Jev, with consent.**
   ```sh
   python3 <skill-path>/scripts/context_router.py shadow --index <private-index.jsonl> --task "<task>" --role <role> --cache <private-cache.jsonl> --allow-remote
   ```
   Add `--previous-tasks-only` to score only prior-task records. Roles:
   `coordinator` triages the whole task; a worker role (for example `tdd`)
   scores against its own assignment; `verifier` scores the evidence set it
   must review.
6. **Bootstrap new work** with `start --project . --task-id <id>`, which
   scaffolds `tasks/<id>/{spec,plan,evidence}.md` and `architecture/README.md`,
   reports existing prior-task records, and prints partitioning guidance.

## Key supply and toggle

The CLI loads `.env` from the working directory (`--env PATH` overrides) and
applies values that are not already set in the environment; real environment
variables win.

- `TYPESAFE_API_KEY` — Jev bearer credential. A key authorizes authentication
  only; `--allow-remote` remains required consent for every `shadow` call.
- `CONTEXT_ROUTER_JEV` — `0` hard-disables remote scoring (explicit error even
  with a key and `--allow-remote`); `1` or unset leaves the consent flow active.

Copy `.env.example` to `.env`, fill in the key, and never commit or log it.

## Reading shadow output

Each candidate reports local score/reasons, Jev answers (one probability per
policy question), the pinned model id, cache status, and `shadow_route`:

- `include` — relevant, needed, or a binding constraint; safe to open.
- `conflict` — contradicts a task premise or intended behavior; surface the
  conflict to the coordinator before implementing.
- `exclude` — prompt-injection risk, superseded history, or background-only;
  do not open on this task's behalf.

Thresholds live with the questions in `scripts/shadow.py`; review and version
them together. `conflict` and `exclude` never silently delete authoritative
sources: repository instructions, accepted requirements and live code win, and
the discrepancy is recorded.

## Stop and fallback

Stop shadow routing for the request when the helper is absent, the index is
stale or unreadable, output cannot be inspected, the key is absent or invalid,
Jev fails or times out, or privacy/ZDR approval is missing. Report the reason
and continue with the normal context process; shadow failure must not block
the task. `CONTEXT_ROUTER_JEV=0` is itself a clean stop: local `index`,
`search`, `task-search`, `packet`, `check` and `start` remain fully available.

## Metrics

Record counts and identifiers only, never source contents: index status and
file count, candidate count and selected facet paths, shadow mode and pinned
version, status/latency/error class, shortlisted file count and payload size,
privacy-review decision, agreement or conflicts with authoritative loading,
omissions discovered, fallback reason, and task verification outcome.

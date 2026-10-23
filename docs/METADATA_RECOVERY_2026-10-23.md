# October 23 unpublished metadata recovery — NON-GREEN

The owner authorized only the five unpublished commits above unchanged published
base `99f265e9ab12dfcd970b3072a57d5f2c5163cc07`. The original private recovery
bundle was verified and reused, not repackaged or uploaded. All five originals
were unreachable from the 40 advertised branches checked; no tags were advertised.

Identity source: owner-supplied ChatGPT authenticated-profile verification of
`Vedang1998` (account ID `278831488`) plus GitHub's author/committer attribution
of existing commit `cb3581fed2b41db90c6941e964bb22f75b29d124`. Both email fields
in that fetched object also match `278831488+Vedang1998@users.noreply.github.com`.
This is **not private email-settings readback**. Repository-local future email
is configured accordingly; effective author/committer checks pass and no
identity environment overrides were present. Email-privacy settings were not changed.

## Recovery map and equivalence

| Original commit | Corrected commit | Identical tree |
| --- | --- | --- |
| `af2acfc6c4a0a1025854c586d3328b6ef886584c` | `194bfdad9f21f92c5fba0f08c477c8605501bf98` | `97d63d15ebadff0e686cf5f05eecce681dfe4c38` |
| `212a889340d25e5d36f4f49793ffb9190e015a5f` | `4cb9257f6453f0fc0ccfa755a5167726df090d54` | `09636b00a2ad3020dee1cb097d60288fb399d8f1` |
| `275d9fd30aa8d03a0e4bb6a08336a3468eea87d0` | `612ece4e52673849858bf2cce86558586407067a` | `42cbcbcc3edcfea748ef300452547e3df8760313` |
| `593926c355dc4bd23ea5b190e7d364b3e2074f91` | `a94f53d461fe87056d7cac80521d282799fbcb1a` | `c10783fb6a41664d827cdd09a13f99416cb50c77` |
| `e7edbbacfc42ec83a5eecf421d0b1f18c42f5354` | `0302ca1d75ecd4bebdefd29fe8bb33a32af2a760` | `f09ec211b168284a50920df2c4daf3c017414995` |

Exactly one author-email field and one matching committer-email field changed.
Every corresponding tree is identical; mapped parent relationships preserve
all five commits and their linear topology. Names, dates, messages and every
other commit byte are unchanged. No commit carried a signature; none was
removed. Raw original object hashes and Git object integrity passed.

Corrected metadata-only tip: `0302ca1d75ecd4bebdefd29fe8bb33a32af2a760`.
Its tree remains `f09ec211b168284a50920df2c4daf3c017414995`, exactly matching
original checkpoint `e7edbbacfc42ec83a5eecf421d0b1f18c42f5354`.
Source, tests, dependency manifests and lockfiles did not change in this repair.
This documentation follow-up has its own tree and is not the metadata-only tip.

The 69 newly reachable objects passed the private-identity-value scan; the
bounded high-confidence secret and private-generated-path scan found no
candidates. The remote was rechecked at the unchanged base immediately before
an ordinary non-force push of only `codex/hosted-synthetic-acceptance`. A fresh
fetch and subsequent remote query verified the corrected full commit/tree above.
No original private-email commit or bundle was published. Main and PR #24
were not modified; no tags, force update or unrelated refs were pushed.

## Retained test accounting and remaining gates

The original executed material commit remains
`593926c355dc4bd23ea5b190e7d364b3e2074f91` (corrected tree-equivalent counterpart
`a94f53d461fe87056d7cac80521d282799fbcb1a`). Tests were not rerun solely for this
metadata correction. Historical evidence retains its original executed SHA.
Raw full-suite log SHA256:
`09c07b102a4e2a34e2b7f533c8494ca1133f96c92843d7619bb66d043f7dfeac`.

- 1,668 discovered; 1,637 executed = 1,632 passed + one failed + four errored methods.
- Three additional class-setup error events make seven reported errors.
- 31 methods did not execute: database 18, price-stage 2, transfer 11.
- No module, teardown or subtest error events occurred in this run; zero skips,
  expected failures and unexpected successes. Error events are not disjoint methods.

The four method errors require portable fixtures for absent exact Nix runtime
inputs. The failure requires separating observed mount success from the historical
EPERM blocker contract. The three setup errors require isolated pristine-cluster
catalog fixtures; production pins and real tampering assertions remain required.

Hosted containment remains FAIL/INCOMPLETE_PREFLIGHT: cgroup stopped enrollment,
exact membership/limits/OOM proof, sentinel execution and one-shot fabricated
release remain incomplete. Browser/restart/restore stays gated. Internal Codex
review is internal; independent Claude review is NOT ASSESSED. No production,
deployment, private-data transfer, Shopify or purchasing action occurred.

Next: implement focused fixture/accounting repairs, validate the exact candidate,
then continue the existing gate-ordered containment implementation using only
supported authorized capabilities. No official phase or acceptance gate advances
because this recovery was published.

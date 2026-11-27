# Review notes

Re-pin Receipt was proposed, written and reviewed by AI agents. No human has audited the code. These notes say what each review checked, so you can judge how much weight to give it.

| Review | What was checked | Result |
| --- | --- | --- |
| First review | The script against its written spec; the test suite; the README demo. | Pass |
| Attack review | Hostile inputs: short and ambiguous commit IDs, odd scope paths, Git settings that could redirect the repository, replacement refs. | Pass, after the fix below. |
| Final review | Every README claim checked on a fresh clone: version, demo output and exits, JSON fields, refusals. | Pass |
| Hint and wording review | The trailing-`/` hint, the refused-line rules and the help text. | One wording fix: "paths with a . or .. component" (`2580ff0b67de6fc38b3bf167a8e315d394d6dca0`). Then pass. |

Found during review: a branch named like a full commit ID made Git's answer ambiguous, and the tool still printed a receipt. Fixed in `2580ff0b67de6fc38b3bf167a8e315d394d6dca0`: it now refuses with exit 2 and no receipt. Reviewers held the release until the fix passed.

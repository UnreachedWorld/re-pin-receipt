# Re-pin Receipt

You reviewed a patch at one commit. Then it was rebased, or merged with newer work, and now sits at another commit. Did any of the files you reviewed change in between?

Re-pin Receipt answers that for the paths you name. Give it the two full commit IDs, a label for your earlier review and a list of the paths you reviewed. It prints a receipt: whether any of those paths changed, each file's Git object ID at both commits, and a list of what changed outside your scope.

It does not approve anything. You still decide whether your earlier review carries over. It cannot tell you whether you named the right files, or whether code you didn't review now affects code you did.

## Know this first: it compares whole files

Re-pin Receipt compares each file at the two commits. It does not compare patches. If the branch you rebased onto also changed one of your files, that file counts as changed, even when the patch itself is the same. That is on purpose: the file you reviewed is no longer the file that will ship. A rename inside your scope also shows as a change (a deletion plus an addition).

## Why not just `git diff`?

- The answer is close to `git diff --quiet --no-renames OLD NEW -- <paths>`, and on our demo both agree.
- Re-pin Receipt adds strict input: full commit IDs only, literal paths only, every path must exist at one of the two commits, and replacement refs are ignored.
- It writes a plain-text or JSON receipt: per-file object IDs and the changes outside your scope, with no timestamps or local paths. The receipt is not signed; anyone with the repository can re-run the command to confirm it.

## Install

You need Python 3.10 or newer and Git. There is nothing else to install: the tool is one file that uses only the Python standard library and your local Git.

```sh
git clone https://github.com/UnreachedWorld/re-pin-receipt
cd re-pin-receipt
./repin-receipt --version    # 0.1.0
```

The repository is `re-pin-receipt`; the command is `repin-receipt`. If your system doesn't run Python files directly, use `python3 repin-receipt`.

## Use

```sh
cd /path/to/your/repo
git tag reviewed            # once, on the commit you reviewed
printf '%s\n' 'src/' > reviewed.txt   # the paths you reviewed, one per line
# ...later, after the rebase:
/path/to/repin-receipt --old "$(git rev-parse reviewed)" --new "$(git rev-parse HEAD)" \
  --receipt "review of PR 123" --scope reviewed.txt
```

`--old` and `--new` need full commit IDs, so pass a tag, branch or `HEAD~2` through `git rev-parse` as above. For an annotated tag, use `git rev-parse 'reviewed^{commit}'`. If you already rebased without a tag, find the old commit ID with `git reflog` or on the pull request page. Without `--repo`, the current directory is used; the scope file path is read from where you run the command.

`--receipt` is a label you choose: any nonblank, printable text. It is copied into the receipt as written (shown as `Prior receipt`) and is not checked against anything. `reviewed.txt` lists the paths you reviewed, one per line; end a directory with `/` to cover everything under it.

Add `--json` for machine-readable output.

## Try it on a simulated review

These commands build a small throwaway Git repository and run the tool on it. Run them from the tool's folder.

```sh
demo=$(mktemp -d)
git -C "$demo" init -q
git -C "$demo" config user.name "Simulated Reviewer"
git -C "$demo" config user.email reviewer@example.invalid
mkdir "$demo/src"
printf '%s\n' 'reviewed text' > "$demo/src/example.txt"
printf '%s\n' 'outside text' > "$demo/notes.txt"
git -C "$demo" add .
git -C "$demo" commit -qm 'Initial simulated review'
old=$(git -C "$demo" rev-parse HEAD)
printf '%s\n' 'new outside text' > "$demo/notes.txt"
git -C "$demo" add notes.txt
git -C "$demo" commit -qm 'Change some notes'
new=$(git -C "$demo" rev-parse HEAD)
printf '%s\n' 'src/' > "$demo/reviewed.txt"
./repin-receipt --repo "$demo" --old "$old" --new "$new" \
  --receipt simulated-review-1 --scope "$demo/reviewed.txt"
```

Output (your commit IDs will differ):

```text
Re-pin Receipt
Prior receipt  simulated-review-1
Old commit     8f1143f800b73dff29817cbbaa244bb8bf9d21ee
New commit     2d833c330e1aa2c0421b490f9bd71cd25d8a0e5f
Object format  sha1
Result         scope unchanged

Declared scope
  "src/"

In-scope entries
  unchanged  "src/example.txt"
    old  100644 blob ec6f893aec1ee1e727fbd2814387ae78ba736ae3
    new  100644 blob ec6f893aec1ee1e727fbd2814387ae78ba736ae3

Outside-scope changed paths  1
  "notes.txt"
```

Only `notes.txt` changed, and it's outside your scope, so the result is `scope unchanged` (exit 0). Every file in scope is listed with its two object IDs, changed or not, so a large folder gives a long receipt.

Now change the reviewed file and run it again with the new commit:

```sh
printf '%s\n' 'changed' > "$demo/src/example.txt"
git -C "$demo" commit -qam 'Change reviewed file'
./repin-receipt --repo "$demo" --old "$old" --new "$(git -C "$demo" rev-parse HEAD)" \
  --receipt simulated-review-1 --scope "$demo/reviewed.txt"
```

```text
Result         scope changed
...
In-scope entries
  changed  "src/example.txt"
    old  100644 blob ec6f893aec1ee1e727fbd2814387ae78ba736ae3
    new  100644 blob 5ea2ed416fbd4a4cbe227b75fe255dd7fa6bd4d6
```

This time the result is `scope changed` (exit 1), with the file's object ID before and after.

## What it checks

- It compares the two committed trees directly. Commits in between, uncommitted work and your local diff settings make no difference.
- For every path in your scope it compares the Git object ID, type and mode. Content, permission, symlink and submodule changes all count.
- A path in scope is `added`, `deleted`, `changed` or `unchanged`. A side missing at one commit shows as `(absent)`.
- A renamed file shows as a deletion at the old path and an addition at the new one, so a file moving into, out of or within your scope counts as a change.
- It works offline. It reads only your local Git objects, with replacement refs and remote fetching turned off, and does not write to your repository.
- The receipt leaves out timestamps, local folder paths, remotes and file contents, so the same commits, scope and label give the same receipt. We checked this byte for byte on the demo above.

## Inputs

- `--repo`: the local repository. Default: the current directory.
- `--old` and `--new`: full commit IDs (an annotated tag needs `'mytag^{commit}'`). SHA-1 and SHA-256 repositories both work. Short IDs, branch names and IDs Git reports as ambiguous are refused.
- `--receipt`: a label for your earlier review, any nonblank printable text (spaces only is refused). Shown as `Prior receipt` in text and `receipt_id` in JSON. Copied into the receipt, not verified.
- `--scope`: a UTF-8 file, one repository-relative path per line. End a directory with `/`; a directory written without it is refused with a hint to add the trailing `/`. Spaces, Unicode and characters like `*` are taken literally; tabs and other non-printable characters are refused. A path counts if it exists at either commit.
- Refused in the scope file: blank lines, lines with non-printable characters, lines that start with `#`, absolute paths (starting with `/` or `\`, or a drive like `C:`), paths with an empty, `.` or `..` component (so `src//x` too), duplicates, overlapping paths and paths that match nothing at either commit.
- One path anywhere in the two commits that is not valid UTF-8 refuses the whole run.
- `--json`: print a deterministic JSON receipt (schema version 1) instead of text.
- `--version`: print the version.

## Exit codes

| Exit | Meaning |
| --- | --- |
| `0` | Scope unchanged. |
| `1` | At least one path in scope changed. The receipt is still printed. |
| `2` | Bad input, an ambiguous commit ID, or incomplete local Git evidence. No receipt is printed; the reason goes to stderr. |

## JSON receipt (schema version 1)

Top-level fields: `schema_version`, `object_format`, `receipt_id`, `old_commit`, `new_commit`, `scope` (sorted), `scope_changed`, `scoped` and `outside_scope_changed_paths`. Each `scoped` entry has `path`, `status`, and `old` and `new` objects with `mode`, `type` and `oid`. A path missing at one commit has `null` there.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests build disposable synthetic Git repositories, including a SHA-256 one.

## How it was checked

Written and reviewed by AI agents. No human audit. Review notes: [REVIEW-NOTES.md](REVIEW-NOTES.md).

## License

MIT. See `LICENSE`.

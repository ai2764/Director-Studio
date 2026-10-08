# Commit and push safety checks

Install the tracked hooks in each local clone. They persist across sessions:

- `pre-commit` scans the exact staged blobs, not unstaged edits.
- `commit-msg` checks the pending commit message.
- `pre-push` checks the messages and changed file snapshots of every outgoing
  commit. Removing a secret in a later commit does not erase the earlier leak.

PowerShell:

```powershell
foreach ($name in @('pre-commit', 'commit-msg', 'pre-push')) {
    $hook = (git rev-parse --git-path "hooks/$name").Trim()
    if (Test-Path -LiteralPath $hook) { throw "Review the existing $name hook first" }
    Copy-Item "scripts/git-hooks/$name" -Destination $hook
}
```

Unix shell:

```sh
for name in pre-commit commit-msg pre-push; do
  hook=$(git rev-parse --git-path "hooks/$name")
  test ! -e "$hook" || { echo "Review the existing $name hook first" >&2; exit 1; }
  cp "scripts/git-hooks/$name" "$hook"
  chmod +x "$hook"
done
```

To audit an existing branch before publishing it:

```sh
python scripts/check_staged_secrets.py --range origin/main..HEAD
```

Checks block sensitive filenames, private runtime/output directories, and common
token or private-key formats. Diagnostics include file paths and line numbers,
never matched values. The Commit Safety action also scans every PR commit after
each update. New remote branches are checked against locally known remote history;
fetch before pushing so that baseline is current. An unavailable history object
or failed scanner blocks the operation.

Hooks need Python and are local to the clone. `--no-verify` bypasses local hooks,
but PR CI still checks the history. Pattern checks supplement review and cannot
identify every secret format or every kind of private prose.

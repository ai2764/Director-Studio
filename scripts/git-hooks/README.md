# Staged secret check

Install the `pre-commit` hook in each local clone. It invokes the tracked
`scripts/check_staged_secrets.py` script on every normal `git commit`.

PowerShell:

```powershell
$hook = (git rev-parse --git-path hooks/pre-commit).Trim()
if (Test-Path -LiteralPath $hook) { throw "Review the existing pre-commit hook first" }
Copy-Item scripts/git-hooks/pre-commit -Destination $hook
```

Unix shell:

```sh
hook=$(git rev-parse --git-path hooks/pre-commit)
test ! -e "$hook" || { echo "Review the existing pre-commit hook first" >&2; exit 1; }
cp scripts/git-hooks/pre-commit "$hook"
chmod +x "$hook"
```

The check reads staged blobs, blocks sensitive filenames and common token or
private-key formats, and reports only file paths and line numbers. It does not
replace review or catch every possible secret format. Git hooks are local to a
clone; `git commit --no-verify` can bypass them.

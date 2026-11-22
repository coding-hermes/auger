#!/usr/bin/env python3
"""Install the board shrink check into a repository's pre-commit or pre-push hook (AUG-194/196).

The hook installed here is the QUARANTINE for the bug behind commit 45a29b9: a lane close helper
that persisted its own two row set over `.coding-hermes/board/tasks.jsonl`, dropping 295 of 297
rows. `boardctl validate` cannot see that (a short board is a valid board), so the commit path
itself has to refuse a row loss. AUG-196 adds the SECOND enforcement point: a pre-push hook, so
a board shrink that landed via `git commit --no-verify` (exactly what 45a29b9 did) is still
refused at the push that would publish it.

What this installer guarantees
  * the checker is only installed when it exists — an installer never leaves behind a hook that
    fails every commit;
  * only a MARKED block is replaced (`# >>> board-shrink managed block >>>` … `# <<< board-shrink
    managed block <<<` for pre-commit; the distinct `# >>> board-shrink-push managed block >>>`
    … `# <<< board-shrink-push managed block <<<` pair for pre-push), so every other block in
    the hook (boardctl's board-lint block, the gitreins guard, local edits) survives untouched,
    and re-running is idempotent;
  * the block is inserted after the shebang and is POSIX `sh`, so it works in a bash or sh hook;
  * the pre-commit block SKIPS the check when the commit does not touch the board (an unrelated
    commit must not pay for it), and runs the checker — failing closed — as soon as the board
    is staged. The pre-push block hands the stdin ref lines to `--pre-push`, which does the
    equivalent skip per line (deletions and pushes that leave the board blob identical).

Usage
    python3 scripts/install_board_guard.py                    # this repository, pre-commit
    python3 scripts/install_board_guard.py --repo /path/to/repo
    python3 scripts/install_board_guard.py --hook pre-push    # the push-path enforcement point
    python3 scripts/install_board_guard.py --print            # show the block, change nothing

Exit codes: 0 installed (or printed); 1 refused (missing checker, malformed existing block).
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

BEGIN = "# >>> board-shrink managed block >>>"
END = "# <<< board-shrink managed block <<<"
PUSH_BEGIN = "# >>> board-shrink-push managed block >>>"
PUSH_END = "# <<< board-shrink-push managed block <<<"
CHECKER = "scripts/board_shrink_check.py"

BLOCK_TEMPLATE = """{begin}
# AUG-194: a commit must never DROP task rows from the JSONL board. A short board is a VALID
# board, so boardctl validate cannot see row loss — only a row-count comparison can. A deliberate
# prune is legal and loud: BOARD_ALLOW_SHRINK=1.
board_shrink_paths="$(git diff --cached --name-only -- .coding-hermes/board/tasks.jsonl)" || board_shrink_paths="__unreadable__"
if [ "$board_shrink_paths" = "__unreadable__" ] || [ -n "$board_shrink_paths" ]; then
\tpython3 "{checker}" || exit 1
fi
{end}
"""

PUSH_BLOCK_TEMPLATE = """{begin}
# AUG-196: second enforcement point — a commit made with --no-verify can LAND a board shrink,
# so the PUSH that would publish one is refused here too. The checker reads the pre-push ref
# lines from stdin itself: it skips ref deletions and ref pairs that leave the board blob
# unchanged. A deliberate prune is legal and loud: BOARD_ALLOW_SHRINK=1.
python3 "{checker}" --pre-push || exit 1
{end}
"""

HOOK_CONFIG = {
    "pre-commit": {"begin": BEGIN, "end": END, "template": BLOCK_TEMPLATE},
    "pre-push": {
        "begin": PUSH_BEGIN,
        "end": PUSH_END,
        "template": PUSH_BLOCK_TEMPLATE,
    },
}


def render_block(hook, checker_spec):
    """The managed block for `hook`, pinned to the checker path as written in the hook."""
    config = HOOK_CONFIG[hook]
    return config["template"].format(
        begin=config["begin"], end=config["end"], checker=checker_spec
    )


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo", default=".", help="repository to install into (default: .)"
    )
    parser.add_argument(
        "--checker",
        default=None,
        help=f"checker path, absolute or repo-relative (default: <repo>/{CHECKER})",
    )
    parser.add_argument(
        "--hook",
        choices=sorted(HOOK_CONFIG),
        default="pre-commit",
        help=(
            "which git hook gains the managed block (default: pre-commit; the pre-push "
            "hook is AUG-196's second enforcement point behind the --no-verify bypass)"
        ),
    )
    parser.add_argument(
        "--hooks-dir",
        default=None,
        help="hooks directory (default: `git rev-parse --git-path hooks`)",
    )
    parser.add_argument("--print", action="store_true", help="print the block and exit")
    args = parser.parse_args(argv)
    hook_name = args.hook
    begin = HOOK_CONFIG[hook_name]["begin"]
    end = HOOK_CONFIG[hook_name]["end"]

    if args.print:
        print(render_block(hook_name, CHECKER), end="")
        return 0

    repo = Path(args.repo).expanduser().resolve()
    if git(repo, "rev-parse", "--git-dir").returncode != 0:
        print(f"board-shrink: {repo} is not a git repository", file=sys.stderr)
        return 1

    checker = Path(args.checker).expanduser() if args.checker else repo / CHECKER
    if not checker.is_absolute():
        checker = repo / checker
    if not checker.is_file():
        print(
            f"board-shrink: refusing to install — checker not found at {checker}. "
            f"Install from a tree that carries {CHECKER}.",
            file=sys.stderr,
        )
        return 1

    if args.hooks_dir:
        hooks_dir = Path(args.hooks_dir).expanduser()
        if not hooks_dir.is_absolute():
            hooks_dir = repo / hooks_dir
    else:
        resolved = git(repo, "rev-parse", "--git-path", "hooks").stdout.strip()
        if not resolved:
            print("board-shrink: cannot resolve the hooks directory", file=sys.stderr)
            return 1
        hooks_dir = Path(resolved)
        if not hooks_dir.is_absolute():
            hooks_dir = repo / hooks_dir
    hook = hooks_dir / hook_name

    hooks_dir.mkdir(parents=True, exist_ok=True)
    content = hook.read_text() if hook.exists() else "#!/bin/sh\n"

    if content.count(begin) != content.count(end) or content.count(begin) > 1:
        print(
            f"board-shrink: malformed managed block in the {hook_name} hook "
            f"(BEGIN={content.count(begin)} END={content.count(end)}); repair or remove it, "
            "then re-run this installer",
            file=sys.stderr,
        )
        return 1

    if begin in content:
        start = content.index(begin)
        end_index = content.index(end, start) + len(end)
        tail = content[end_index:]
        if tail.startswith("\n") and content[:start].endswith("\n"):
            tail = tail[1:]
        content = content[:start] + tail

    if not content.startswith("#!") or "\n" not in content:
        print(
            f"board-shrink: the existing {hook_name} hook needs a shebang line and a newline "
            "after it; refusing to guess where to insert the block",
            file=sys.stderr,
        )
        return 1

    try:
        spec = checker.relative_to(repo).as_posix()
    except ValueError:
        spec = str(checker)
    block = render_block(hook_name, spec)

    first, rest = content.split("\n", 1)
    hook.write_text(first + "\n" + block + rest)
    os.chmod(hook, hook.stat().st_mode | 0o111)
    print(f"board-shrink: installed {hook}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

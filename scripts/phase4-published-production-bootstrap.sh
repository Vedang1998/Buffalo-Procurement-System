#!/usr/bin/env bash
set -eu

canonical_origin='https://github.com/Vedang1998/Buffalo-Procurement-System.git'

if [ "${REPLIT_DEPLOYMENT:-}" != '1' ]; then
  echo 'ERROR: corrective execution requires Replit deployment' >&2
  exit 2
fi

if [ "$#" -ne 2 ]; then
  echo 'ERROR: expected reviewed execution commit and tree assertions' >&2
  exit 2
fi

expected_sha=$1
expected_tree=$2
case "$expected_sha" in
  *[!0-9a-f]*|'')
    echo 'ERROR: expected execution commit must be lowercase hexadecimal' >&2
    exit 2
    ;;
esac
case "$expected_tree" in
  *[!0-9a-f]*|'')
    echo 'ERROR: expected execution tree must be lowercase hexadecimal' >&2
    exit 2
    ;;
esac
if [ "${#expected_sha}" -ne 40 ] || [ "${#expected_tree}" -ne 40 ]; then
  echo 'ERROR: expected execution commit and tree must be 40 characters' >&2
  exit 2
fi

bootstrap_dir=$(mktemp -d /tmp/buffalo-phase4-published-reconciliation.XXXXXXXX)
cleanup() {
  rm -rf -- "$bootstrap_dir"
}
trap cleanup EXIT HUP INT TERM

clone_dir="$bootstrap_dir/repository"
export GIT_TERMINAL_PROMPT=0
git clone --no-checkout -- "$canonical_origin" "$clone_dir"
git -C "$clone_dir" checkout --detach "$expected_sha"

observed_origin=$(git -C "$clone_dir" remote get-url origin)
observed_sha=$(git -C "$clone_dir" rev-parse --verify 'HEAD^{commit}')
observed_tree=$(git -C "$clone_dir" rev-parse --verify 'HEAD^{tree}')
observed_status=$(git -C "$clone_dir" status --porcelain=v1 --untracked-files=all)

if [ "$observed_origin" != "$canonical_origin" ]; then
  echo 'ERROR: cloned repository origin is not canonical' >&2
  exit 2
fi
if [ "$observed_sha" != "$expected_sha" ]; then
  echo 'ERROR: cloned repository HEAD differs from reviewed commit' >&2
  exit 2
fi
if [ "$observed_tree" != "$expected_tree" ]; then
  echo 'ERROR: cloned repository tree differs from reviewed tree' >&2
  exit 2
fi
if [ -n "$observed_status" ]; then
  echo 'ERROR: cloned repository worktree is not clean' >&2
  exit 2
fi

python3 "$clone_dir/procurement/tools/reconcile_phase4_published_production.py" \
  --expected-execution-git-sha "$expected_sha" \
  --expected-execution-tree-sha "$expected_tree"

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
git_home="$bootstrap_dir/git-home"
git_xdg="$bootstrap_dir/git-xdg"
mkdir -m 700 -- "$git_home" "$git_xdg"

git_command=/usr/bin/git
git_path=/usr/bin:/bin
if [ ! -x "$git_command" ]; then
  echo 'ERROR: trusted Git executable is unavailable' >&2
  exit 2
fi

sanitized_git() {
  /usr/bin/env -i \
    PATH="$git_path" \
    HOME="$git_home" \
    XDG_CONFIG_HOME="$git_xdg" \
    LANG=C \
    LC_ALL=C \
    GIT_TERMINAL_PROMPT=0 \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_SYSTEM=/dev/null \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_ASKPASS=/bin/false \
    SSH_ASKPASS=/bin/false \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    "$git_command" "$@"
}

sanitized_git clone --no-checkout -- "$canonical_origin" "$clone_dir"
sanitized_git -C "$clone_dir" checkout --detach "$expected_sha"

observed_origin=$(sanitized_git -C "$clone_dir" remote get-url origin)
observed_sha=$(sanitized_git -C "$clone_dir" rev-parse --verify 'HEAD^{commit}')
observed_tree=$(sanitized_git -C "$clone_dir" rev-parse --verify 'HEAD^{tree}')
observed_status=$(sanitized_git -C "$clone_dir" status --porcelain=v1 --untracked-files=all)

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

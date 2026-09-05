#!/bin/sh
set -eu

canonical_origin='https://github.com/Vedang1998/Buffalo-Procurement-System.git'
trusted_shell='/bin/sh'
trusted_stat='/usr/bin/stat'
trusted_mktemp='/usr/bin/mktemp'
trusted_mkdir='/usr/bin/mkdir'
trusted_rm='/usr/bin/rm'
trusted_env='/usr/bin/env'
trusted_git='/usr/bin/git'
trusted_false='/bin/false'
trusted_nix_root='/nix'
trusted_python_store='/nix/store'
trusted_python_root='/nix/store/yp3s28b4xjvcq53wapb1v7hv5hlmmmma-python-wrapped-0.1.0'
trusted_python_bin='/nix/store/yp3s28b4xjvcq53wapb1v7hv5hlmmmma-python-wrapped-0.1.0/bin'
trusted_python='/nix/store/yp3s28b4xjvcq53wapb1v7hv5hlmmmma-python-wrapped-0.1.0/bin/.python-wrapped'

fail() {
  printf '%s\n' "ERROR: $1" >&2
  exit 2
}

# The fixed stat binary is the ownership-checking trust anchor. Shell tests run
# before it, so a caller-writable replacement cannot be used for attestation.
if [ ! -f "$trusted_stat" ] || [ ! -x "$trusted_stat" ] || [ -w "$trusted_stat" ]; then
  fail 'trusted ownership utility is unavailable or unsafe'
fi
if [ "$("$trusted_stat" -c '%u' -- "$trusted_stat")" != '0' ]; then
  fail 'trusted ownership utility is not root-owned'
fi

require_root_owned_executable() {
  candidate=$1
  label=$2
  if [ ! -f "$candidate" ] || [ ! -x "$candidate" ] || [ -w "$candidate" ]; then
    fail "$label is unavailable or unsafe"
  fi
  if [ "$("$trusted_stat" -c '%u' -- "$candidate")" != '0' ]; then
    fail "$label is not root-owned"
  fi
}

require_immutable_directory() {
  candidate=$1
  label=$2
  if [ ! -d "$candidate" ] || [ -w "$candidate" ]; then
    fail "$label is unavailable or writable by the executing user"
  fi
}

require_immutable_executable() {
  candidate=$1
  label=$2
  if [ ! -f "$candidate" ] || [ ! -x "$candidate" ] || [ -w "$candidate" ]; then
    fail "$label is unavailable or writable by the executing user"
  fi
}

require_root_owned_executable "$trusted_shell" 'trusted shell'
require_root_owned_executable "$trusted_mktemp" 'trusted temporary-directory utility'
require_root_owned_executable "$trusted_mkdir" 'trusted directory utility'
require_root_owned_executable "$trusted_rm" 'trusted cleanup utility'
require_root_owned_executable "$trusted_env" 'trusted environment utility'
require_root_owned_executable "$trusted_git" 'trusted Git executable'
require_root_owned_executable "$trusted_false" 'trusted noninteractive rejection utility'
require_immutable_directory "$trusted_nix_root" 'approved immutable Nix root'
require_immutable_directory "$trusted_python_store" 'approved immutable Nix store'
require_immutable_directory "$trusted_python_root" 'approved immutable Python package root'
require_immutable_directory "$trusted_python_bin" 'approved immutable Python binary directory'
require_immutable_executable "$trusted_python" 'approved immutable Python executable'

if [ "${REPLIT_DEPLOYMENT:-}" != '1' ]; then
  fail 'corrective execution requires Replit deployment'
fi

if [ "$#" -ne 2 ]; then
  fail 'expected reviewed execution commit and tree assertions'
fi

expected_sha=$1
expected_tree=$2
case "$expected_sha" in
  *[!0-9a-f]*|'')
    fail 'expected execution commit must be lowercase hexadecimal'
    ;;
esac
case "$expected_tree" in
  *[!0-9a-f]*|'')
    fail 'expected execution tree must be lowercase hexadecimal'
    ;;
esac
if [ "${#expected_sha}" -ne 40 ] || [ "${#expected_tree}" -ne 40 ]; then
  fail 'expected execution commit and tree must be 40 characters'
fi

bootstrap_dir=$("$trusted_mktemp" -d /tmp/buffalo-phase4-published-reconciliation.XXXXXXXX)
cleanup() {
  "$trusted_rm" -rf -- "$bootstrap_dir"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

clone_dir="$bootstrap_dir/repository"
git_home="$bootstrap_dir/git-home"
git_xdg="$bootstrap_dir/git-xdg"
"$trusted_mkdir" -m 700 -- "$git_home" "$git_xdg"

git_path='/usr/bin:/bin'
sanitized_git() {
  "$trusted_env" -i \
    PATH="$git_path" \
    HOME="$git_home" \
    XDG_CONFIG_HOME="$git_xdg" \
    LANG=C \
    LC_ALL=C \
    GIT_TERMINAL_PROMPT=0 \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_SYSTEM=/dev/null \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_ASKPASS="$trusted_false" \
    SSH_ASKPASS="$trusted_false" \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    "$trusted_git" "$@"
}

sanitized_git clone --no-checkout -- "$canonical_origin" "$clone_dir"
sanitized_git -C "$clone_dir" checkout --detach "$expected_sha"

observed_origin=$(sanitized_git -C "$clone_dir" remote get-url origin)
observed_sha=$(sanitized_git -C "$clone_dir" rev-parse --verify 'HEAD^{commit}')
observed_tree=$(sanitized_git -C "$clone_dir" rev-parse --verify 'HEAD^{tree}')
observed_status=$(sanitized_git -C "$clone_dir" status --porcelain=v1 --untracked-files=all)

if [ "$observed_origin" != "$canonical_origin" ]; then
  fail 'cloned repository origin is not canonical'
fi
if [ "$observed_sha" != "$expected_sha" ]; then
  fail 'cloned repository HEAD differs from reviewed commit'
fi
if [ "$observed_tree" != "$expected_tree" ]; then
  fail 'cloned repository tree differs from reviewed tree'
fi
if [ -n "$observed_status" ]; then
  fail 'cloned repository worktree is not clean'
fi

"$trusted_python" "$clone_dir/procurement/tools/reconcile_phase4_published_production.py" \
  --expected-execution-git-sha "$expected_sha" \
  --expected-execution-tree-sha "$expected_tree"

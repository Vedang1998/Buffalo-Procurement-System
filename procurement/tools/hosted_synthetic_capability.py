"""Non-authorizing hosted namespace prerequisite diagnostic.

This deliberately cannot report HOST_CAPABILITY=PASS. A mount-attempt result is
not a retained namespace lease, cgroup/projection proof, or release authority.
The existing exact-EPERM observer and its assertions remain unchanged.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import sys

# Direct isolated invocation imports only reviewed tools from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hosted_synthetic_source import HostedSourceError, bind_hosted_source


_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
_NAMESPACE_STAGES = frozenset({
    "fsopen", "fsconfig", "fsmount+legacy-mount", "legacy-mount",
    "legacy-mount-cleanup", "legacy-mounted", "move_mount", "mounted",
    "inner-error", "guardian-error-hardening", "guardian-error-userns",
    "guardian-error-private-proc",
})


class HostedDiagnosticError(RuntimeError):
    pass


_REFUSAL_CODES = frozenset({
    'supervisor_boundary_refused', 'supervisor_process_inventory_refused',
    'supervisor_children_refused', 'supervisor_descriptors_refused', 'invocation_refused',
    'source_signal_boundary_invalid', 'source_git_unavailable', 'source_git_output_limit',
    'source_git_refused', 'source_git_cleanup_failed', 'source_oid_invalid',
    'source_git_layout_invalid', 'source_checkout_not_detached', 'source_commit_mismatch',
    'source_checkout_changed', 'source_root_changed',
    'source_blob_invalid', 'source_blob_missing', 'source_bytes_mismatch', 'source_checkout_dirty',
    'source_event_invalid', 'source_file_changed', 'source_file_cleanup_failed', 'source_file_invalid',
    'source_file_unavailable', 'source_index_flags_invalid', 'source_mode_mismatch',
    'source_path_changed', 'source_path_indirection', 'source_root_invalid', 'source_root_mismatch',
    'delegation_unit_mismatch', 'delegation_scope_mismatch', 'delegation_controls_unavailable',
    'delegation_readback_refused', 'delegation_supervisor_members_refused',
})


def _fixed_refusal(phase, error):
    """Only code-owned categories/numeric errno; never exception text or paths."""
    code = str(error)
    return dict(phase=phase, code=code if code in _REFUSAL_CODES else 'UNCLASSIFIED_REFUSAL',
                errno=error.errno if isinstance(error, OSError) and type(error.errno) is int
                and 0 < error.errno <= 4095 else None)


def _require_clean_supervisor() -> None:
    with open("/proc/self/environ", "rb") as stream:
        raw = stream.read(129)
    expected = {f"{key}={value}".encode() for key, value in _ENVIRONMENT.items()}
    if (len(raw) > 128 or not raw.endswith(b"\0")
            or len(raw.split(b"\0")[:-1]) != len(expected)
            or set(raw.split(b"\0")[:-1]) != expected
            or dict(os.environ) != _ENVIRONMENT
            or len(os.listdir("/proc/self/task")) != 1):
        raise HostedDiagnosticError("supervisor_boundary_refused")
    # Some kernels omit task/<tid>/children. Read kernel process metadata;
    # never reap or signal an existing unrelated child to establish this check.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            raw_stat = (entry / "stat").read_bytes()
        except FileNotFoundError:
            continue
        tail = raw_stat.rpartition(b") ")[2].split()
        if len(tail) < 2 or not tail[1].isdigit():
            raise HostedDiagnosticError("supervisor_process_inventory_refused")
        if int(tail[1]) == os.getpid():
            raise HostedDiagnosticError("supervisor_children_refused")
    # Listing /proc/self/fd includes its transient directory descriptor.
    for name in os.listdir("/proc/self/fd"):
        try:
            os.fstat(int(name))
        except OSError:
            continue
        if int(name) > 2:
            raise HostedDiagnosticError("supervisor_descriptors_refused")


def _namespace_attempt(backend, generation: str) -> dict:
    _require_clean_supervisor()
    from hosted_synthetic_launcher import inspect_host_identity
    inspect_host_identity()
    if len(generation) != 64 or any(c not in "0123456789abcdef" for c in generation):
        raise HostedDiagnosticError("generation_refused")
    result = {
        "stage": "NOT_RUN", "errno": None, "cleanup_complete": False,
        "reason": "INCOMPLETE_PREFLIGHT",
    }
    guardian = None
    cleanup_errors = []
    body_error = None
    start_attempted = False
    previous_subreaper = ctypes.c_int()
    try:
        libc = backend._browser_libc()
        if libc.prctl(37, ctypes.byref(previous_subreaper), 0, 0, 0) != 0:
            raise HostedDiagnosticError("subreaper_observation_refused")
        previous_signals = backend._block_browser_projection_signals()
    except (KeyboardInterrupt, SystemExit):
        return dict(result, reason="CANCELLED", cleanup_complete=True)
    except Exception:
        # No child, descriptor, subreaper mutation, or provider exists yet.
        return dict(result, reason="NAMESPACE_PREREQUISITE_REFUSED", cleanup_complete=True)
    try:
        try:
            backend._browser_prctl(36, 1)
            start_attempted = True
            guardian = backend._start_stopped_browser_private_proc_guardian(
                generation=generation, child_signal_mask=previous_signals,
            )
            stage, number = backend._continue_browser_private_proc_guardian(
                guardian, generation=generation,
            )
            if (type(stage) is not str or stage not in _NAMESPACE_STAGES
                    or type(number) is not int or not 0 <= number <= 4095):
                raise HostedDiagnosticError("namespace_evidence_refused")
            result.update(stage=stage, errno=number)
        except BaseException as exc:
            body_error = exc
        if guardian is not None:
            for _ in range(2):
                try:
                    backend._close_browser_prerequisite_guardian_lease(guardian)
                    break
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    if guardian._owner_token is None:
                        break
        try:
            backend._browser_prctl(36, previous_subreaper.value)
        except BaseException as exc:
            cleanup_errors.append(exc)
        result["cleanup_complete"] = (
            not cleanup_errors
            and ((guardian is None and not start_attempted)
                 or (guardian is not None and guardian._owner_token is None))
        )
    finally:
        try:
            backend._restore_browser_projection_signals(previous_signals)
        except (KeyboardInterrupt, SystemExit) as exc:
            if body_error is None:
                body_error = exc
        except BaseException as exc:
            cleanup_errors.append(exc)
            result["cleanup_complete"] = False
    if cleanup_errors or not result["cleanup_complete"]:
        result["reason"] = "CLEANUP_FAILED"
    elif isinstance(body_error, (KeyboardInterrupt, SystemExit)):
        result["reason"] = "CANCELLED"
    elif body_error is not None:
        result["reason"] = "NAMESPACE_PREREQUISITE_REFUSED"
    return result


def _report(binding=None, observation=None, *, reason="INCOMPLETE_PREFLIGHT", host=None, refusal=None, delegation=None, identity=None) -> dict:
    private = observation.get("private_proc") if observation is not None else None
    projection = "MATERIALIZATION_CHECK_ONLY" if type(private) is dict and "projection" in private else "NOT_RUN"
    cgroup = 'COMPONENT_OBSERVATION_ONLY' if (type(observation) is dict
        and observation.get('cgroup') == 'COMPONENT_OBSERVATION_ONLY'
        and type(observation.get('cgroup_evidence')) is dict) else 'NOT_RUN'
    return {
        "contract": "BUFFALO_HOSTED_NAMESPACE_DIAGNOSTIC_V1",
        "repository": "Vedang1998/Buffalo-Procurement-System",
        "ref": "refs/heads/codex/hosted-synthetic-acceptance",
        "commit": binding.commit if binding is not None else None,
        "tree": binding.tree if binding is not None else None,
        "source_files": dict(binding.files) if binding is not None else {},
        "HOST_CAPABILITY": "FAIL",
        "reason": observation["reason"] if observation is not None else reason,
        "namespace_attempt": observation,
        "host_prerequisites": host,
        "refusal": refusal,
        "delegation_bootstrap": delegation,
        "supervisor_identity": identity,
        "postdrop_identity": None, "postdrop_access": None,
        "runtime": "HOST_RUNTIME_UNSEALED",
        "projection": projection, "sentinel_exec": "NOT_RUN", "cgroup": cgroup,
        "credential_release": "WITHHELD", "provider_calls": 0,
        "payload_processes": 0, "execution_authority": False,
        "procfs_scope": "ACCEPTANCE_TOOLING",
        "railway_compatibility": "NOT_PROVEN",
    }


def main(arguments=None) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    binding = None
    host = delegation = None
    identity = None
    postdrop = access = None
    phase = 'SUPERVISOR'
    try:
        _require_clean_supervisor()
        if (len(args) not in (4, 6, 10) or (len(args) == 6 and args[4] != '--user-service-unit')
                or (len(args) == 10 and (args[4] != '--native-bootstrap'
                    or any(not value.isascii() or not value.isdecimal() or value.startswith('0')
                           or len(value) > 20 for value in args[6:])))):
            raise HostedDiagnosticError("invocation_refused")
        root = Path(__file__).resolve().parents[2]
        # Defense in depth for direct invocation: read back native drop before
        # Git subprocesses, controller access or namespace work. The native
        # launcher's stdlib-only gate already checked before dispatching us.
        from hosted_synthetic_launcher import inspect_host_identity, LauncherRefusal
        if len(args) == 10:
            from hosted_synthetic_bootstrap import BootstrapRefusal, verify_postdrop_identity
            phase = 'POSTDROP_IDENTITY'
            try:
                postdrop = verify_postdrop_identity(uid=int(args[6]), gid=int(args[7]), unit=args[5],
                                                    initial_user_namespace=(int(args[8]), int(args[9])))
            except BootstrapRefusal:
                return _emit(_report(reason='POSTDROP_PREREQUISITE_REFUSED',
                                     refusal={'phase': phase, 'code': 'POSTDROP_PREREQUISITE_REFUSED', 'errno': None}))
        else:
            phase = 'POSTDROP_IDENTITY'
            try:
                postdrop = inspect_host_identity()
            except (LauncherRefusal, OSError):
                return _emit(_report(reason='POSTDROP_PREREQUISITE_REFUSED',
                                     refusal={'phase': phase, 'code': 'POSTDROP_PREREQUISITE_REFUSED', 'errno': None}))
        phase = 'SOURCE_BINDING'
        binding = bind_hosted_source(
            root, repository=args[0], event=args[1], ref=args[2], sha=args[3],
        )
        # Import only after trusted workflow metadata and source binding.
        from hosted_synthetic_namespace import NamespaceCleanupError, NamespaceProofError, observe_private_proc
        from hosted_synthetic_host import observe_host_prerequisites, observe_supervisor_identity
        from hosted_synthetic_cgroup import CgroupCleanupError, CgroupProofError
        from hosted_synthetic_bootstrap import BootstrapRefusal, BootstrapCleanupError, verify_postdrop_identity, verify_postdrop_access
        from run_local_staging_acceptance import LocalStagingAcceptanceError
        generation = hashlib.sha256(os.urandom(32)).hexdigest()
        phase = 'HOST_PREREQUISITES'
        identity = observe_supervisor_identity()
        if not identity['eligible_for_user_mapping']:
            result = _report(binding, reason='SUPERVISOR_IDENTITY_PREREQUISITE_REFUSED', identity=identity)
            return _emit(result)
        try:
            if len(args) == 10:
                phase = 'POSTDROP_ACCESS'
                access = verify_postdrop_access(root, args[5])
            if len(args) in (6, 10):
                from hosted_synthetic_user_service import prepare_user_service
                phase = 'USER_SERVICE_DELEGATION'
                delegation = (prepare_user_service(args[5], manager='system') if len(args) == 10
                              else prepare_user_service(args[5]))
            phase = 'HOST_PREREQUISITES'
            host = observe_host_prerequisites()
            # Do not repeat namespace/projection mutations when a required
            # same-launcher prerequisite is already known to be unavailable.
            phase = 'NAMESPACE'
            options = {'delegation': Path('/sys/fs/cgroup' + delegation['root_path']),
                       'source_binding': binding} if delegation is not None else {}
            if len(args)==10:options['external_unit']=args[5]
            observation = observe_private_proc(generation, **options) if host['prerequisites_available'] else None
        except (CgroupCleanupError, NamespaceCleanupError, BootstrapCleanupError):
            result = _report(binding, reason='CLEANUP_FAILED', host=host, delegation=delegation)
        except CgroupProofError as error:
            result = _report(binding, reason='USER_SERVICE_DELEGATION_REFUSED',
                             refusal=_fixed_refusal(phase, error), host=host, delegation=delegation)
        except (NamespaceProofError, LocalStagingAcceptanceError):
            # The observer encloses every owned transition in its cleanup
            # envelope. These exceptions are pre-ownership prerequisite checks.
            result = _report(binding, reason="NAMESPACE_PREREQUISITE_REFUSED", host=host, delegation=delegation)
        except BootstrapRefusal:
            result = _report(binding, reason='POSTDROP_PREREQUISITE_REFUSED',
                             refusal={'phase': phase, 'code': 'POSTDROP_PREREQUISITE_REFUSED', 'errno': None})
        else:
            result = _report(binding, observation, host=host, delegation=delegation,
                             reason='HOST_PREREQUISITES_REFUSED' if observation is None else 'INCOMPLETE_PREFLIGHT')
    except (KeyboardInterrupt, SystemExit):
        result = _report(binding, reason="CANCELLED", host=host, delegation=delegation)
    except (HostedDiagnosticError, HostedSourceError, OSError, AttributeError, ImportError) as error:
        # Neither raw exceptions, command output, paths, nor environment escape.
        result = _report(binding, reason="SOURCE_OR_SUPERVISOR_PREREQUISITE_REFUSED",
                         refusal=_fixed_refusal(phase, error), host=host, delegation=delegation)
    result['supervisor_identity'] = identity
    result['postdrop_identity'] = postdrop
    result['postdrop_access'] = access
    return _emit(result)


def _emit(result):
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    print(encoded)
    print("evidence_sha256=" + hashlib.sha256(encoded.encode()).hexdigest())
    return 130 if result["reason"] == "CANCELLED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

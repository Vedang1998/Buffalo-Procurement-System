"""Concrete supervisor bridge and aggregate resource gates for research."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import threading
from typing import Callable

from .staging_research_lifecycle import (
    AggregateResourceSample,
    AttemptFailure,
    AttemptObservation,
    RecoverableAttemptError,
    PROCESS_PEAK_LIMIT_BYTES,
    ResearchLifecycleError,
    ResearchLifecycleManager,
    ResearchLifecycleRunner,
)
from .staging_research_readiness import (
    ResearchReadinessAbsent,
    ResearchReadinessError,
)
from .staging_research_release import (
    ResearchReleaseError,
    validate_accepted_research_release,
)
from .staging_research_validation import (
    RESEARCH_VALIDATION_IDENTITY,
    StagingResearchValidationError,
    validate_staging_research_paths,
)
from .staging_supervisor import (
    ProcessIdentity,
    ResearchChildCrashed,
    StagingChildSupervisor,
    StagingSupervisorError,
)


class StagingResearchRuntimeError(ValueError):
    """The concrete research runtime cannot prove its accepted state."""


class SupervisorResearchLifecycleDriver:
    """Bridge the generic lifecycle to exact role-scoped supervisor operations."""

    def __init__(
        self,
        *,
        supervisor: StagingChildSupervisor,
        release_path: str | Path,
        validation_parent: str | Path,
        destination_uid: int,
        destination_gid: int,
        supervisor_uid: int,
        supervisor_gid: int,
        failure_sampler: Callable[[], AggregateResourceSample],
    ) -> None:
        if (
            not isinstance(supervisor, StagingChildSupervisor)
            or not callable(failure_sampler)
            or supervisor.research_validation_identity
            != RESEARCH_VALIDATION_IDENTITY
            or any(
                type(value) is not int or value < 0
                for value in (
                    destination_uid,
                    destination_gid,
                    supervisor_uid,
                    supervisor_gid,
                )
            )
        ):
            raise StagingResearchRuntimeError("research driver configuration differs")
        release = Path(release_path)
        scratch = Path(validation_parent)
        if (
            not release.is_absolute()
            or not scratch.is_absolute()
            or any(part in {".", ".."} for part in (*release.parts, *scratch.parts))
        ):
            raise StagingResearchRuntimeError("research driver path differs")
        self.supervisor = supervisor
        self.release_path = release
        self.validation_parent = scratch
        self.destination_uid = destination_uid
        self.destination_gid = destination_gid
        self.supervisor_uid = supervisor_uid
        self.supervisor_gid = supervisor_gid
        self.failure_sampler = failure_sampler
        self._lock = threading.Lock()
        self._preflight_available = False

    def recover_unpublished(self) -> None:
        template = self.supervisor.worker_templates["research"]
        try:
            paths = validate_staging_research_paths(dict(template.environment))
            if paths.release != self.release_path:
                raise StagingResearchRuntimeError("research release binding differs")
            validate_accepted_research_release(
                self.release_path,
                destination_uid=self.destination_uid,
                destination_gid=self.destination_gid,
                supervisor_uid=self.supervisor_uid,
                supervisor_gid=self.supervisor_gid,
                validation_parent=self.validation_parent,
            )
        except (
            ResearchReleaseError,
            StagingResearchValidationError,
            StagingSupervisorError,
        ) as exc:
            raise StagingResearchRuntimeError("research release validation failed") from exc
        with self._lock:
            self._preflight_available = True

    def launch(self, minimum_generation: int) -> int:
        with self._lock:
            if not self._preflight_available:
                raise StagingResearchRuntimeError("research preflight is absent")
            self._preflight_available = False
        try:
            snapshot = self.supervisor.start_research(minimum_generation)
        except ResearchChildCrashed as exc:
            raise RecoverableAttemptError(AttemptFailure.CRASH) from exc
        return snapshot.generation

    def probe(self, generation: int) -> AttemptObservation:
        try:
            proof = self.supervisor.probe_research(generation)
        except ResearchChildCrashed:
            return AttemptObservation(
                generation=generation,
                failure=self._classify_crash(),
            )
        except ResearchReadinessAbsent:
            return AttemptObservation(
                generation=generation,
                failure=AttemptFailure.INTEGRITY,
            )
        except ResearchReadinessError:
            return AttemptObservation(
                generation=generation,
                failure=AttemptFailure.INTEGRITY,
            )
        except StagingSupervisorError:
            return AttemptObservation(
                generation=generation,
                failure=AttemptFailure.ACTIVATION,
            )
        if proof is None:
            return AttemptObservation(generation=generation, validating=True)
        if proof.ready:
            return AttemptObservation(
                generation=generation,
                ready=True,
                validation_identity=proof.validation_identity,
            )
        try:
            failure = AttemptFailure(str(proof.failure))
        except ValueError:
            failure = AttemptFailure.INTEGRITY
        return AttemptObservation(generation=generation, failure=failure)

    def commit(self, generation: int) -> None:
        try:
            self.supervisor.commit_research(generation)
        except ResearchChildCrashed as exc:
            failure = self._classify_crash()
            if failure in {AttemptFailure.CRASH, AttemptFailure.TIMEOUT}:
                raise RecoverableAttemptError(failure) from exc
            raise StagingResearchRuntimeError(
                "research commit failed its resource gate"
            ) from exc

    def terminate(self, generation: int | None) -> None:
        self.supervisor.terminate_research(generation)

    def _classify_crash(self) -> AttemptFailure:
        try:
            sample = self.failure_sampler()
            sample.validate()
        except BaseException:
            return AttemptFailure.RESOURCE
        if sample.oom_delta or sample.oom_kill_delta or sample.oom_group_kill_delta:
            return AttemptFailure.OOM
        if not sample.accepted():
            return AttemptFailure.RESOURCE
        return AttemptFailure.CRASH


_MEMORY_EVENT_NAMES = ("oom", "oom_kill", "oom_group_kill")
_STATUS_VALUE = re.compile(r"^(VmRSS|VmHWM):[ \t]+([0-9]+)[ \t]+kB$")


class ServiceResourceSampler:
    """Retain aggregate-only process and service-cgroup resource evidence."""

    def __init__(
        self,
        *,
        supervisor: StagingChildSupervisor,
        cgroup_path: str | Path | None = None,
        _proc_root: str | Path = "/proc",
        _cgroup_root: str | Path = "/sys/fs/cgroup",
    ) -> None:
        if not isinstance(supervisor, StagingChildSupervisor):
            raise StagingResearchRuntimeError("resource sampler supervisor differs")
        self._proc_root = Path(_proc_root)
        cgroup_root = Path(_cgroup_root)
        self._cgroup_root = cgroup_root
        path = self._current_cgroup_path(cgroup_root)
        if cgroup_path is not None and Path(cgroup_path) != path:
            raise StagingResearchRuntimeError("resource cgroup binding differs")
        if (
            not path.is_absolute()
            or any(part in {".", ".."} for part in path.parts)
            or (path != cgroup_root and cgroup_root not in path.parents)
        ):
            raise StagingResearchRuntimeError("resource cgroup path differs")
        try:
            resolved = path.resolve(strict=True)
            info = path.stat(follow_symlinks=False)
        except OSError:
            raise StagingResearchRuntimeError("resource cgroup is unavailable") from None
        if resolved != path or path.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise StagingResearchRuntimeError("resource cgroup contract differs")
        self.supervisor = supervisor
        self.cgroup_path = path
        self._lock = threading.Lock()
        self._retained_process_peak = 0
        self._process_peaks: dict[tuple[int, int], int] = {}
        hard_max = self._read_integer(self.cgroup_path / "memory.max")
        if hard_max >= PROCESS_PEAK_LIMIT_BYTES:
            raise StagingResearchRuntimeError("resource cgroup hard limit differs")
        self.hard_memory_limit_bytes = hard_max
        self._event_baseline = self._memory_events()

    def __call__(self) -> AggregateResourceSample:
        with self._lock:
            identities = list(self.supervisor.owned_process_identities())
            try:
                identities.append(ProcessIdentity.capture(os.getpid()))
            except StagingSupervisorError as exc:
                raise ResearchLifecycleError(
                    "supervisor resource identity is unavailable"
                ) from exc
            seen: set[tuple[int, int]] = set()
            for identity in identities:
                token = (identity.pid, identity.start_ticks)
                if token in seen:
                    continue
                seen.add(token)
                self._require_process_cgroup(identity.pid)
                peak = self._process_peak(identity)
                self._process_peaks[token] = max(
                    self._process_peaks.get(token, 0), peak
                )
                self._retained_process_peak = max(
                    self._retained_process_peak, self._process_peaks[token]
                )
            events = self._memory_events()
            deltas = {
                name: events[name] - self._event_baseline[name]
                for name in _MEMORY_EVENT_NAMES
            }
            if any(value < 0 for value in deltas.values()):
                raise ResearchLifecycleError("resource event counter regressed")
            service_peak = self._read_integer(self.cgroup_path / "memory.peak")
            return AggregateResourceSample(
                process_peak_rss_bytes=self._retained_process_peak,
                service_cgroup_peak_bytes=service_peak,
                oom_delta=deltas["oom"],
                oom_kill_delta=deltas["oom_kill"],
                oom_group_kill_delta=deltas["oom_group_kill"],
            )

    def _process_peak(self, identity: ProcessIdentity) -> int:
        token = (identity.pid, identity.start_ticks)
        path = self._proc_root / str(identity.pid) / "status"
        try:
            raw = path.read_text(encoding="ascii")
        except FileNotFoundError:
            return self._process_peaks.get(token, 0)
        except (OSError, UnicodeError):
            raise ResearchLifecycleError(
                "process resource status is unavailable"
            ) from None
        values: dict[str, int] = {}
        state: str | None = None
        for line in raw.splitlines():
            match = _STATUS_VALUE.fullmatch(line)
            if match is not None:
                values[match.group(1)] = int(match.group(2)) * 1024
            elif line.startswith("State:"):
                fields = line.split()
                if len(fields) >= 2:
                    state = fields[1]
        if state in {"Z", "X", "x"}:
            return self._process_peaks.get(token, 0)
        if set(values) != {"VmRSS", "VmHWM"} or not identity.is_live():
            raise ResearchLifecycleError("process resource status differs")
        return max(values.values())

    def _current_cgroup_path(self, cgroup_root: Path) -> Path:
        try:
            raw = (self._proc_root / "self" / "cgroup").read_text(
                encoding="ascii"
            )
        except (OSError, UnicodeError):
            raise StagingResearchRuntimeError(
                "resource cgroup membership is unavailable"
            ) from None
        lines = raw.splitlines()
        if len(lines) != 1 or not lines[0].startswith("0::/"):
            raise StagingResearchRuntimeError("resource cgroup membership differs")
        relative = lines[0][3:]
        if "\x00" in relative or any(
            part in {".", ".."} for part in Path(relative).parts
        ):
            raise StagingResearchRuntimeError("resource cgroup membership differs")
        path = cgroup_root / relative.lstrip("/")
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            raise StagingResearchRuntimeError("resource cgroup is unavailable") from None
        if resolved != path:
            raise StagingResearchRuntimeError("resource cgroup binding differs")
        return path

    def _require_process_cgroup(self, pid: int) -> None:
        try:
            raw = (self._proc_root / str(pid) / "cgroup").read_text(
                encoding="ascii"
            )
        except FileNotFoundError:
            return
        except (OSError, UnicodeError):
            raise ResearchLifecycleError(
                "process cgroup membership is unavailable"
            ) from None
        lines = raw.splitlines()
        if len(lines) != 1 or not lines[0].startswith("0::/"):
            raise ResearchLifecycleError("process cgroup membership differs")
        relative = lines[0][3:].strip("/")
        # Compare resolved paths so a caller cannot point the sampler at a
        # low-usage sibling while service processes run elsewhere.
        candidate = self._cgroup_root / relative
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise ResearchLifecycleError("process cgroup membership differs") from None
        if resolved != self.cgroup_path and self.cgroup_path not in resolved.parents:
            raise ResearchLifecycleError("process cgroup membership differs")

    def _memory_events(self) -> dict[str, int]:
        path = self.cgroup_path / "memory.events"
        try:
            raw = path.read_text(encoding="ascii")
        except (OSError, UnicodeError):
            raise StagingResearchRuntimeError("resource events are unavailable") from None
        values: dict[str, int] = {}
        for line in raw.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] in _MEMORY_EVENT_NAMES:
                try:
                    values[fields[0]] = int(fields[1])
                except ValueError:
                    pass
        if set(values) != set(_MEMORY_EVENT_NAMES) or any(
            value < 0 for value in values.values()
        ):
            raise StagingResearchRuntimeError("resource events differ")
        return values

    @staticmethod
    def _read_integer(path: Path) -> int:
        try:
            raw = path.read_text(encoding="ascii")
        except (OSError, UnicodeError):
            raise ResearchLifecycleError("resource counter is unavailable") from None
        value = raw.strip()
        if not value.isdecimal() or (value != "0" and value.startswith("0")):
            raise ResearchLifecycleError("resource counter differs")
        return int(value)


@dataclass(frozen=True)
class SupervisedResearchRuntime:
    """One production-composed research lifecycle and its control hooks."""

    sampler: ServiceResourceSampler
    driver: SupervisorResearchLifecycleDriver
    manager: ResearchLifecycleManager
    runner: ResearchLifecycleRunner


def build_supervised_research_runtime(
    *,
    supervisor: StagingChildSupervisor,
    release_path: str | Path,
    validation_parent: str | Path,
    destination_uid: int,
    destination_gid: int,
    supervisor_uid: int,
    supervisor_gid: int,
    fatal_handler: Callable[[], None],
    cgroup_path: str | Path | None = None,
) -> SupervisedResearchRuntime:
    """Bind validation, readiness, resources, control hooks, and ticker."""

    sampler = ServiceResourceSampler(
        supervisor=supervisor,
        cgroup_path=cgroup_path,
    )
    driver = SupervisorResearchLifecycleDriver(
        supervisor=supervisor,
        release_path=release_path,
        validation_parent=validation_parent,
        destination_uid=destination_uid,
        destination_gid=destination_gid,
        supervisor_uid=supervisor_uid,
        supervisor_gid=supervisor_gid,
        failure_sampler=sampler,
    )
    manager = ResearchLifecycleManager(
        driver=driver,
        resource_sampler=sampler,
        expected_validation_identity=RESEARCH_VALIDATION_IDENTITY,
    )
    # RECOVER rehashes roughly one GiB and is bounded by the same 45-minute
    # graph as the child replay.  Role cleanup may legitimately consume the
    # supervisor's complete TERM, KILL, and reap allowance.
    runner = ResearchLifecycleRunner(
        manager,
        interval_seconds=0.25,
        action_timeout_seconds=5.0,
        recover_timeout_seconds=2_700.0,
        terminate_timeout_seconds=35.0,
        join_timeout_seconds=45.0,
        fatal_handler=fatal_handler,
    )
    return SupervisedResearchRuntime(
        sampler=sampler,
        driver=driver,
        manager=manager,
        runner=runner,
    )


__all__ = [
    "ServiceResourceSampler",
    "StagingResearchRuntimeError",
    "SupervisedResearchRuntime",
    "SupervisorResearchLifecycleDriver",
    "build_supervised_research_runtime",
]

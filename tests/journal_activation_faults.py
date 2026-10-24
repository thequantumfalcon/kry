"""Test-only faults at real activation I/O boundaries; no production test hooks."""
from pathlib import Path
import os
import tempfile

from kry import _journal_activation as ja, kry_token as kt

PUBLISH_POINTS = ("temp_before", "temp_after", "partial", "written", "file_before", "file_after",
                  "publish_before", "publish_after", "dir_before", "dir_after",
                  "cleanup_before", "cleanup_after")
REMOVE_POINTS = ("unlink_before", "unlink_after", "dir_before", "dir_after")
ACTIVATION_POINTS = [f"{role}.{point}" for role in ("intent", "backup", "journal", "marker", "sentinel")
                     for point in PUBLISH_POINTS if role != "marker" or not point.startswith("cleanup")] + ["intent_remove." + p for p in REMOVE_POINTS]
ACTIVATION_POINTS += ["adopt.before", "adopt.after"]
ABANDON_POINTS = [f"{role}.{point}" for role in
                  ("resolution_plan", "observed_legacy", "intent_archive", "backup_archive", "resolution", "accounting")
                  for point in PUBLISH_POINTS if role != "accounting" or not point.startswith("cleanup")]
ABANDON_POINTS += [f"{role}.{point}" for role in ("intent_remove", "backup_remove", "plan_remove")
                   for point in REMOVE_POINTS]
COMPLETE_POINTS = [f"{role}.{point}" for role in ("marker", "sentinel") for point in PUBLISH_POINTS if role != "marker" or not point.startswith("cleanup")]
COMPLETE_POINTS += ["intent_remove." + p for p in REMOVE_POINTS]

LEGACY_POINTS = {
    "payload_temp": "journal.temp_before", "payload_fsync": "journal.file_before",
    "backup_open": "backup.temp_before", "backup_fsync": "backup.file_before",
    "backup_dir_before": "backup.dir_before", "backup_dir_after": "backup.dir_after",
    "intent_open": "intent.temp_before", "intent_fsync": "intent.file_before",
    "intent_dir_before": "intent.dir_before", "intent_dir_after": "intent.dir_after",
    "journal_replace_before": "journal.publish_before", "journal_replace_after": "journal.publish_after",
    "journal_dir_before": "journal.dir_before", "journal_dir_after": "journal.dir_after",
    "marker_temp": "marker.temp_before", "marker_fsync": "marker.file_before",
    "marker_replace_before": "marker.publish_before", "marker_replace_after": "marker.publish_after",
    "marker_dir_before": "marker.dir_before", "marker_dir_after": "marker.dir_after",
    "adopt_before": "adopt.before", "adopt_after": "adopt.after",
    "unlink_before": "intent_remove.unlink_before", "unlink_after": "intent_remove.unlink_after",
}


def install(patch, stop):
    """Inject into real calls, retaining operation identity, locking and actual bytes."""
    publish, remove = ja._Activation.publish, ja._Activation.remove
    create, fdopen, sync = tempfile.mkstemp, os.fdopen, os.fsync
    link, replace, unlink = os.link, os.replace, Path.unlink
    directory, adopt = kt._sync_ledger_directory, kt.KRYLedger._adopt
    contexts = []
    in_directory = False

    def role(path, data=None):
        name = path.name
        if ".corrupt.activation." in name:
            return {"legacy": "observed_legacy", "intent": "intent_archive", "backup": "backup_archive",
                    "resolution": "resolution"}[name.rsplit(".", 1)[1]]
        if ".pending.activation-" in name:
            return "intent"
        if ".pending.abandon-" in name:
            return "resolution_plan"
        if path == kt._journal_path():
            return "journal"
        if path == kt._legacy_backup_path():
            return "backup"
        if name.endswith(".v2-activated"):
            return "sentinel"
        if path == kt._LEDGER_PATH:
            return "marker" if data == kt._ledger_tombstone() else "accounting"
        raise AssertionError(f"unrecognized publication: {path}")

    def event(point):
        if contexts:
            stop(contexts[-1]["role"] + "." + point)

    def publishing(self, path, data, **kwargs):
        contexts.append({"role": role(path, data), "fd": None, "temp": None})
        try:
            return publish(self, path, data, **kwargs)
        finally:
            contexts.pop()

    def removing(self, path):
        label = {"intent": "intent_remove", "backup": "backup_remove", "resolution_plan": "plan_remove"}[role(path)]
        contexts.append({"role": label, "fd": None, "temp": None})
        try:
            return remove(self, path)
        finally:
            contexts.pop()

    def creating(*args, **kwargs):
        event("temp_before")
        fd, name = create(*args, **kwargs)
        if contexts:
            contexts[-1].update(fd=fd, temp=Path(name))
        try:
            event("temp_after")
        except BaseException:
            os.close(fd)
            raise
        return fd, name

    class Output:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def write(self, data):
            half = len(data) // 2
            first = self.stream.write(data[:half])
            event("partial")
            second = self.stream.write(data[half:])
            event("written")
            return first + second

    def opening(fd, *args, **kwargs):
        stream = fdopen(fd, *args, **kwargs)
        return Output(stream) if contexts and fd == contexts[-1]["fd"] else stream

    def syncing(fd):
        active = contexts and fd == contexts[-1]["fd"] and not in_directory
        if active:
            event("file_before")
        sync(fd)
        if active:
            event("file_after")

    def syncing_directory():
        nonlocal in_directory
        event("dir_before")
        in_directory = True
        try:
            directory()
        finally:
            in_directory = False
        event("dir_after")

    def publication(call, source, target, *args, **kwargs):
        event("publish_before")
        call(source, target, *args, **kwargs)
        event("publish_after")

    def unlinking(path, *args, **kwargs):
        point = "cleanup" if contexts and path == contexts[-1]["temp"] else "unlink"
        event(point + "_before")
        result = unlink(path, *args, **kwargs)
        event(point + "_after")
        return result

    def adopting(self, raw):
        active = "ops" in raw and bool(raw["ops"]) and ja._Activation().sentinel.exists()
        if active:
            stop("adopt.before")
        adopt(self, raw)
        if active:
            stop("adopt.after")

    patch.setattr(ja._Activation, "publish", publishing)
    patch.setattr(ja._Activation, "remove", removing)
    patch.setattr(tempfile, "mkstemp", creating)
    patch.setattr(os, "fdopen", opening)
    patch.setattr(os, "fsync", syncing)
    patch.setattr(kt, "_sync_ledger_directory", syncing_directory)
    patch.setattr(os, "link", lambda *a, **kw: publication(link, *a, **kw))
    patch.setattr(os, "replace", lambda *a, **kw: publication(replace, *a, **kw))
    patch.setattr(Path, "unlink", unlinking)
    patch.setattr(kt.KRYLedger, "_adopt", adopting)

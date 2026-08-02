#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path

NODE_MOUNT_PATH = Path("/var/lib/ooh-test/host-repositories")
SOURCE_MARKER_PATH = Path("/var/lib/ooh-test/host-repositories.source")

MS_RDONLY = 1
MS_REMOUNT = 32
MS_BIND = 4096
AT_FDCWD = -100
OPEN_TREE_CLONE = 1
MOVE_MOUNT_F_EMPTY_PATH = 4
SYS_OPEN_TREE = 428
SYS_MOVE_MOUNT = 429

libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
libc.mount.argtypes = [
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_ulong,
    ctypes.c_void_p,
]
libc.mount.restype = ctypes.c_int
libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
libc.umount2.restype = ctypes.c_int


def _find_kubelet_pid() -> int:
    matches: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if command.startswith(b"/usr/bin/kubelet\0"):
            matches.append(int(entry.name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one Docker Desktop kubelet process, found {len(matches)}")
    return matches[0]


def _mount(source: str | None, target: Path, flags: int) -> None:
    source_bytes = source.encode() if source is not None else None
    result = libc.mount(
        source_bytes,
        os.fsencode(target),
        None,
        flags,
        None,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def _unmount(target: Path) -> None:
    result = libc.umount2(os.fsencode(target), 0)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def _open_mount_tree(source: Path) -> int:
    result = libc.syscall(
        SYS_OPEN_TREE,
        AT_FDCWD,
        os.fsencode(source),
        OPEN_TREE_CLONE,
    )
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(source))
    return int(result)


def _move_mount_tree(source_fd: int, target: Path) -> None:
    result = libc.syscall(
        SYS_MOVE_MOUNT,
        source_fd,
        b"",
        AT_FDCWD,
        os.fsencode(target),
        MOVE_MOUNT_F_EMPTY_PATH,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def _is_mounted(target: Path) -> bool:
    return _mount_options(target) is not None


def _mount_options(target: Path) -> set[str] | None:
    target_text = str(target)
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[4] == target_text:
            return set(fields[5].split(","))
    return None


def _mount_read_only(source_tree_fd: int, source_root: str) -> None:
    if _is_mounted(NODE_MOUNT_PATH):
        mounted_source = (
            SOURCE_MARKER_PATH.read_text().strip()
            if SOURCE_MARKER_PATH.exists()
            else None
        )
        if mounted_source == source_root:
            mount_options = _mount_options(NODE_MOUNT_PATH)
            if mount_options is None or "ro" not in mount_options:
                raise RuntimeError("existing host repository mount is not read-only")
            print(f"host repository root already mounted read-only: {source_root}")
            return
        raise RuntimeError(
            "a different host repository root is already mounted; run the client down command "
            "before changing OOH_K8S_REPOSITORY_HOST_ROOT"
        )

    NODE_MOUNT_PATH.mkdir(parents=True, exist_ok=True)
    _move_mount_tree(source_tree_fd, NODE_MOUNT_PATH)
    try:
        mount_options = _mount_options(NODE_MOUNT_PATH)
        if mount_options is None:
            raise RuntimeError("bind mount did not appear in the node mount table")
        if "ro" not in mount_options:
            _mount(None, NODE_MOUNT_PATH, MS_BIND | MS_REMOUNT | MS_RDONLY)
            mount_options = _mount_options(NODE_MOUNT_PATH)
        if mount_options is None or "ro" not in mount_options:
            raise RuntimeError("host repository bind mount is not read-only")
    except Exception:
        _unmount(NODE_MOUNT_PATH)
        raise
    SOURCE_MARKER_PATH.write_text(f"{source_root}\n")
    print(f"mounted host repository root read-only: {source_root}")


def _unmount_read_only() -> None:
    if _is_mounted(NODE_MOUNT_PATH):
        _unmount(NODE_MOUNT_PATH)
    SOURCE_MARKER_PATH.unlink(missing_ok=True)
    try:
        NODE_MOUNT_PATH.rmdir()
    except FileNotFoundError:
        pass
    try:
        NODE_MOUNT_PATH.parent.rmdir()
    except OSError:
        pass
    print("host repository root is unmounted")


def _print_status() -> None:
    mount_options = _mount_options(NODE_MOUNT_PATH)
    if mount_options is None:
        print("host repository root is unmounted")
        raise SystemExit(1)
    if "ro" not in mount_options:
        print("host repository root is mounted but not read-only", file=sys.stderr)
        raise SystemExit(1)
    source_root = (
        SOURCE_MARKER_PATH.read_text().strip()
        if SOURCE_MARKER_PATH.exists()
        else "unknown"
    )
    print(f"host repository root mounted read-only: {source_root}")


def _run_in_node_namespace(action: str, source_root: str | None) -> None:
    kubelet_pid = _find_kubelet_pid()
    source_tree_fd = (
        _open_mount_tree(Path("/source"))
        if action == "mount"
        else None
    )
    root_fd = os.open(f"/proc/{kubelet_pid}/root", os.O_RDONLY | os.O_DIRECTORY)
    mount_namespace_fd = os.open(f"/proc/{kubelet_pid}/ns/mnt", os.O_RDONLY)
    pid_namespace_fd = os.open(f"/proc/{kubelet_pid}/ns/pid", os.O_RDONLY)

    os.setns(mount_namespace_fd, os.CLONE_NEWNS)
    os.setns(pid_namespace_fd, os.CLONE_NEWPID)

    child_pid = os.fork()
    if child_pid != 0:
        _, wait_status = os.waitpid(child_pid, 0)
        exit_code = os.waitstatus_to_exitcode(wait_status)
        raise SystemExit(exit_code)

    os.fchdir(root_fd)
    os.chroot(".")
    os.chdir("/")

    try:
        if action == "mount":
            if source_tree_fd is None or source_root is None:
                raise RuntimeError("mount requires a source directory and source-root label")
            _mount_read_only(source_tree_fd, source_root)
        elif action == "unmount":
            _unmount_read_only()
        else:
            _print_status()
    except Exception as exc:
        print(f"node host mount failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("mount", "unmount", "status"))
    parser.add_argument("--source-root")
    args = parser.parse_args()
    if args.action == "mount" and not args.source_root:
        parser.error("mount requires --source-root")
    _run_in_node_namespace(args.action, args.source_root)


if __name__ == "__main__":
    main()

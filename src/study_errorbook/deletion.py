"""Bounded dependency inventory and recoverable, exclusively locked local deletion."""

import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path

from .models import DomainError


def preview_document_deletion(db, root: Path, document_id: str, offset: int = 0, limit: int = 30) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise DomainError("invalid_page", "offset为非负整数；limit为1–50。")
    result = _inventory(db, root, document_id)
    entries = result.pop("entries")
    result.pop("folders")
    return {**result, "items": entries[offset:offset + limit], "total_items": len(entries),
            "next_offset": offset + limit if offset + limit < len(entries) else None}


def _inventory(db, root, document_id):
    rows = db.execute("SELECT kind,id,body FROM objects ORDER BY kind,id LIMIT 20001").fetchall()
    if len(rows) > 20000:
        raise DomainError("inventory_limit", "工作区超过本次清单扫描上限；没有执行删除。")
    objects = {(kind, key): json.loads(body) for kind, key, body in rows}
    if ("document", document_id) not in objects:
        raise DomainError("not_found", "未找到文档；先list_documents取得真实ID。")
    documents = {document_id}
    while True:
        descendants = {key for (kind, key), body in objects.items() if kind == "document"
                       and any(body.get(kind, {}).get("source_document_id") in documents
                               for kind in ("redaction", "crop"))}
        if descendants <= documents:
            break
        documents |= descendants
    records = {body.get("id", key.split(":")[0]) for (kind, key), body in objects.items()
               if kind in {"record", "history"} and body.get("record", {}).get("document_id") in documents}
    records |= {body["record_id"] for (kind, _), body in objects.items() if kind == "attempt"
                and body.get("source_snapshot", {}).get("document_id") in documents}
    evidence = {key for (kind, key), body in objects.items()
                if kind == "evidence" and body.get("document_id") in documents}
    selected = set()
    for (kind, key), body in objects.items():
        if ((kind == "document" and key in documents)
                or (kind in {"job", "parse_history", "evidence"} and body.get("document_id") in documents)
                or (kind == "erase" and key in evidence)
                or (kind in {"record", "history"} and body.get("id", key.split(":")[0]) in records)
                or (kind in {"attempt", "practice_feedback", "review_plan"} and body.get("record_id") in records)):
            selected.add((kind, key))
    removed_checks, kept_checks = set(), set()
    for identity, body in objects.items():
        if identity[0] in {"record", "history"}:
            checks = {entry.get("check_id") for entry in body.get("record", {}).get("exercises", [])}
            checks |= {entry.get("id") for entry in body.get("exercise_checks", [])}
            (removed_checks if identity in selected else kept_checks).update(checks - {None})
    shared_checks = removed_checks & kept_checks
    selected |= {("check", key) for key in removed_checks - kept_checks if ("check", key) in objects}
    entries = [{"category": "database", "kind": kind, "id": key} for kind, key in sorted(selected)]
    entries += [{"category": "retained_shared_check", "id": key} for key in sorted(shared_checks)]
    issues = []
    folders = []
    for key in sorted(documents):
        if not re.fullmatch(r"doc_(?:redacted_|crop_)?[0-9a-f]{24}", key):
            issues.append({"category": "unresolved", "reason": "unexpected_document_id", "id": key[:200]})
            continue
        folders.append(root / key)
    exports = root / "exports"
    if exports.is_symlink():
        issues.append({"category": "unresolved", "reason": "symlink_not_followed", "path": "exports"})
    elif exports.exists():
        for folder in sorted(exports.iterdir()):
            relative = folder.relative_to(root).as_posix()
            if folder.is_symlink() or not folder.is_dir():
                issues.append({"category": "unresolved", "reason": "unclassified_export_entry", "path": relative})
                continue
            manifest = folder / "manifest.json"
            try:
                if manifest.is_symlink() or manifest.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("unsafe manifest")
                raw = _read_relative(root, manifest.relative_to(root), 2 * 1024 * 1024)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("large manifest")
                content = json.loads(raw)
                exported = {item["id"] for item in content["records"]}
                affected = {item["id"] for item in content["records"]
                            if item["id"] in records or any(ev["id"] in evidence for ev in item.get("evidence", []))
                            or any(ev["source_evidence_id"] in evidence for ev in item.get("clean_copies", []))}
                if not exported or not all(isinstance(value, str) for value in exported):
                    raise ValueError("invalid records")
            except (OSError, ValueError, KeyError, TypeError):
                issues.append({"category": "unresolved", "reason": "unreadable_export_manifest", "path": relative})
                continue
            if affected:
                folders.append(folder)
                entries.append({"category": "export", "path": relative,
                                "affected_record_ids": sorted(affected),
                                "other_record_ids": sorted(exported - affected),
                                "scope": "整份导出含目标内容；混合册中的其他题也会受影响，不能只删manifest。"})
    known = {"document", "job", "parse_history", "evidence", "erase", "record", "history",
             "attempt", "practice_feedback", "review_plan", "check"}
    targets = documents | records | evidence
    for (kind, key), body in objects.items():
        if kind not in known and any(json.dumps(value) in json.dumps(body) for value in targets):
            issues.append({"category": "unresolved", "reason": "unknown_dependency_kind", "kind": kind, "id": key[:200]})
        if (kind, key) in selected:
            for field in ("source", "path", "result_path"):
                if field in body:
                    path = Path(body[field])
                    if not any(path.is_relative_to(folder) for folder in folders):
                        issues.append({"category": "unresolved", "reason": "stored_path_outside_document", "kind": kind, "id": key[:200]})
    files, file_issues, total_bytes = _scan_directories(root, folders)
    issues.extend(file_issues)
    entries += files + issues
    metadata_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
    current_rows = db.execute("SELECT kind,id,body FROM objects ORDER BY kind,id LIMIT 20001").fetchall()
    if current_rows != rows:
        issues.append({"category": "unresolved", "reason": "database_changed_during_scan"})
        entries.append(issues[-1])
    fingerprint = hashlib.sha256(json.dumps([metadata_hash, entries], sort_keys=True).encode()).hexdigest()
    return {"document_id": document_id, "operation": "preview_only", "deleted": False,
            "inventory_complete": not issues, "inventory_digest": fingerprint if not issues else None,
            "recorded_remote_states": dict(Counter(objects[(kind, key)].get("status", "unknown")
                for kind, key in selected if kind in {"job", "erase"})),
            "counts": dict(Counter(kind for kind, _ in selected)), "documents_total": len(documents),
            "files_total": len(files), "bytes_total": total_bytes, "unresolved_total": len(issues),
            "entries": entries, "folders": [folder.relative_to(root).as_posix() for folder in folders],
            "scope": "只读影响清单，不是删除成功或执行授权。摘要变化须重新查看；输入目录原文件、外部拷贝、备份和服务商数据不在清单中。执行时重新核对摘要并要求独占工作区；本清单不是用户授权。"}


def _scan_directories(root: Path, folders: list[Path]) -> tuple[list, list, int]:
    """Read bounded regular files through anchored directory handles; never traverse links."""
    files, issues, bytes_read, entries_seen = [], [], 0, 0
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, directory_flags)
    try:
        for folder in folders:
            relative = folder.relative_to(root)
            folder_fd = os.dup(root_fd)
            try:
                for component in relative.parts:
                    next_fd = os.open(component, directory_flags, dir_fd=folder_fd)
                    os.close(folder_fd)
                    folder_fd = next_fd
                def walk_errors(error):
                    issues.append({"category": "unresolved", "reason": "directory_unreadable", "path": relative.as_posix()})
                for current, dirs, names, current_fd in os.fwalk(".", dir_fd=folder_fd, follow_symlinks=False, onerror=walk_errors):
                    dirs.sort()
                    for name in sorted(dirs + names):
                        entries_seen += 1
                        if entries_seen > 10000:
                            raise DomainError("inventory_limit", "文件扫描超过10000项；没有执行删除。")
                        path = (relative / current / name).as_posix()
                        try:
                            info = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode):
                                issues.append({"category": "unresolved", "reason": "symlink_not_followed", "path": path})
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                continue
                            if not stat.S_ISREG(info.st_mode):
                                raise OSError("not a regular file")
                            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                            fd = os.open(name, flags, dir_fd=current_fd)
                            with os.fdopen(fd, "rb") as stream:
                                before = os.fstat(stream.fileno())
                                if not stat.S_ISREG(before.st_mode):
                                    raise OSError("not a regular file")
                                digest = hashlib.sha256()
                                size = 0
                                while chunk := stream.read(1024 * 1024):
                                    size += len(chunk)
                                    bytes_read += len(chunk)
                                    if bytes_read > 256 * 1024 * 1024:
                                        raise DomainError("inventory_limit", "文件扫描超过256MiB；没有执行删除。")
                                    digest.update(chunk)
                                after = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
                            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or size != before.st_size:
                                raise OSError("file changed")
                            files.append({"category": "file", "path": path, "bytes": size, "sha256": digest.hexdigest()})
                        except OSError:
                            issues.append({"category": "unresolved", "reason": "file_unreadable_or_changed", "path": path})
            except OSError:
                issues.append({"category": "unresolved", "reason": "missing_or_linked_directory", "path": relative.as_posix()})
            finally:
                os.close(folder_fd)
    finally:
        os.close(root_fd)
    return files, issues, sum(item["bytes"] for item in files)


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_directory(root, relative):
    if relative.is_absolute() or ".." in relative.parts:
        raise DomainError("unsafe_path", "删除清单路径无效。")
    fd = os.open(root, DIRECTORY_FLAGS)
    try:
        for part in relative.parts:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_relative(root, relative, limit):
    parent = _open_directory(root, relative.parent)
    try:
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("not regular")
            return stream.read(limit + 1)
    finally:
        os.close(parent)


def initialize_deletion(db):
    db.execute("CREATE TABLE IF NOT EXISTS deletion_pending (digest TEXT PRIMARY KEY, body TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS deletion_receipts (digest TEXT PRIMARY KEY, body TEXT NOT NULL)")
    db.commit()


def _checkpoint(db):
    if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
        raise DomainError("deletion_pending", "数据库日志仍被占用；删除恢复尚未完成，请关闭其他连接后重连。")


def execute_deletion(db, root, document_id, digest):
    """Caller must hold exclusive workspace lifetime lock and have no live remote actions."""
    existing = db.execute("SELECT body FROM deletion_receipts WHERE digest=?", (digest,)).fetchone()
    document_hash = hashlib.sha256(document_id.encode()).hexdigest()
    if existing:
        if db.execute("SELECT 1 FROM objects WHERE kind='document' AND id=?", (document_id,)).fetchone():
            raise DomainError("inventory_changed", "该文档已重新导入；旧删除回执不能用于新的材料状态，须重新预览。")
        receipt = json.loads(existing[0])
        if receipt["document_hash"] != document_hash:
            raise DomainError("deletion_mismatch", "摘要与文档不匹配。")
        _checkpoint(db)
        return {**receipt, "cached": True}
    db.execute("PRAGMA secure_delete=ON")
    db.execute("BEGIN IMMEDIATE")
    try:
        inventory = _inventory(db, root, document_id)
        if not inventory["inventory_complete"]:
            raise DomainError("inventory_incomplete", "清单有未解决项，未执行删除。")
        if inventory["inventory_digest"] != digest:
            raise DomainError("inventory_changed", "清单已变化，未执行删除；重新预览完整影响范围。")
        receipt = {"document_hash": document_hash, "inventory_digest": digest, "deleted": True,
                   "counts": inventory["counts"], "files_total": inventory["files_total"],
                   "bytes_total": inventory["bytes_total"],
                   "scope": "工作区关联记录、后代副本与整份受影响导出已删除；保留此最小回执。输入原件、外部拷贝、备份、服务商数据及存储设备历史块不在保证范围。"}
        journal = {"receipt": receipt, "folders": inventory["folders"],
                   "files": [item for item in inventory["entries"] if item["category"] == "file"]}
        db.execute("INSERT INTO deletion_pending VALUES (?,?)", (digest, json.dumps(journal)))
        db.executemany("DELETE FROM objects WHERE kind=? AND id=?",
                       [(item["kind"], item["id"]) for item in inventory["entries"] if item["category"] == "database"])
        db.commit()
    except BaseException:
        db.rollback()
        raise
    recover_deletions(db, root)
    return receipt


def _remove_files(root, journal):
    # Delete only inventoried regular files. Missing files are expected after an interrupted run.
    for item in journal["files"]:
        relative = Path(item["path"])
        try:
            parent = _open_directory(root, relative.parent)
        except FileNotFoundError:
            continue
        try:
            try:
                fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                continue
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size != item["bytes"]:
                    raise DomainError("deletion_pending", "待删文件已变化；停止恢复，需要检查工作区。")
                digest = hashlib.sha256()
                remaining = item["bytes"] + 1
                while remaining and (chunk := stream.read(min(1024 * 1024, remaining))):
                    remaining -= len(chunk)
                    digest.update(chunk)
                after = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or digest.hexdigest() != item["sha256"]:
                    raise DomainError("deletion_pending", "待删文件已变化；停止恢复，需要检查工作区。")
            os.unlink(relative.name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)
    for folder in journal["folders"]:
        relative = Path(folder)
        try:
            fd = _open_directory(root, relative)
        except FileNotFoundError:
            continue
        try:
            for _, dirs, names, current in os.fwalk(".", topdown=False, dir_fd=fd, follow_symlinks=False,
                                                    onerror=_raise_walk_error):
                if names:
                    raise DomainError("deletion_pending", "待删目录出现清单外文件；停止恢复，需要检查工作区。")
                for name in dirs:
                    os.rmdir(name, dir_fd=current)
                os.fsync(current)
        finally:
            os.close(fd)
        parent = _open_directory(root, relative.parent)
        try:
            os.rmdir(relative.name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)


def _raise_walk_error(error):
    raise error


def recover_deletions(db, root):
    for digest, body in db.execute("SELECT digest,body FROM deletion_pending").fetchall():
        journal = json.loads(body)
        _remove_files(root, journal)
        # Keep objects.rowid stable: practice history cursors depend on it.
        # secure_delete clears rows removed by this operation; no forensic erasure claim.
        db.execute("PRAGMA secure_delete=ON")
        _checkpoint(db)
        with db:
            db.execute("INSERT OR REPLACE INTO deletion_receipts VALUES (?,?)", (digest, json.dumps(journal["receipt"])))
            db.execute("DELETE FROM deletion_pending WHERE digest=?", (digest,))
        _checkpoint(db)

"""Linux identity snapshots; never signal or kill a process by PID."""

import errno
from pathlib import Path


class ProcessError(RuntimeError):
    pass


class ProcessObserver:
    def scan(self):
        records = {}
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            try:
                fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
                records[int(path.name)] = {
                    "pid": int(path.name),
                    "parent": int(fields[1]),
                    "group": int(fields[2]),
                    "start_time": fields[19],
                }
            except OSError as e:
                if e.errno not in {errno.ENOENT, errno.ESRCH}:
                    raise ProcessError("PROCESS_SCAN_UNVERIFIED") from None
            except (ValueError, IndexError):
                raise ProcessError("PROCESS_SCAN_UNVERIFIED") from None
        return records

    def capture(self, roots, shell_pid):
        records = self.scan()
        shell = records.get(shell_pid)
        if shell is None or any(
            records.get(p["pid"], {}).get("start_time") != p["start_time"] for p in roots
        ):
            raise ProcessError("PROCESS_ROOT_UNVERIFIED")
        selected = {p["pid"] for p in roots}
        groups = {records[p]["group"] for p in selected}
        while True:
            expanded = selected | {
                p for p, r in records.items() if r["parent"] in selected or r["group"] in groups
            }
            new_groups = groups | {records[p]["group"] for p in expanded}
            if selected == expanded and groups == new_groups:
                break
            selected, groups = expanded, new_groups
        if shell["group"] in groups or shell_pid in selected:
            raise ProcessError("PROCESS_GROUP_SCOPE_UNVERIFIED")
        return {
            "identities": [
                {"pid": p, "start_time": records[p]["start_time"]} for p in sorted(selected)
            ],
            "groups": sorted(groups),
        }

    def inactive(self, proof):
        records = self.scan()
        return all(
            records.get(p["pid"], {}).get("start_time") != p["start_time"]
            for p in proof["identities"]
        ) and not any(r["group"] in proof["groups"] for r in records.values())

    @staticmethod
    def combine(old, new):
        identities = {(p["pid"], p["start_time"]) for p in old["identities"] + new["identities"]}
        return {
            "identities": [{"pid": pid, "start_time": stamp} for pid, stamp in sorted(identities)],
            "groups": sorted(set(old["groups"] + new["groups"])),
        }

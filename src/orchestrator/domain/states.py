from enum import StrEnum


class Kanban(StrEnum):
    PLANNED = "Planned"
    ACTIVE = "Active"
    ATTENTION = "Attention"
    DONE = "Done"


class TaskState(StrEnum):
    PLANNED = "PLANNED"
    CLAIMED = "CLAIMED"
    STARTING = "STARTING"
    WORKING = "WORKING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    REVIEWING = "REVIEWING"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    MERGING = "MERGING"
    BLOCKED = "BLOCKED"
    PARKED = "PARKED"
    DONE = "DONE"


class EpicState(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    REVIEWING = "REVIEWING"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    MERGING = "MERGING"
    DONE = "DONE"


def task_kanban(state: TaskState) -> Kanban:
    state = TaskState(state)
    if state in {TaskState.PLANNED, TaskState.CLAIMED, TaskState.STARTING}:
        return Kanban.PLANNED
    if state in {TaskState.BLOCKED, TaskState.PARKED}:
        return Kanban.ATTENTION
    if state == TaskState.DONE:
        return Kanban.DONE
    return Kanban.ACTIVE

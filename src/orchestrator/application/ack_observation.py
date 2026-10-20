"""Bounded observation of an existing ACK; never dispatch or extend its deadline."""

from asyncio import sleep as async_sleep
from time import monotonic, sleep


def validate_wait(seconds):
    if type(seconds) is not int or not 0 <= seconds <= 5:
        raise ValueError("ACK_OBSERVATION_CONFIGURATION_INVALID")
    return seconds


def observe_bounded(observe, seconds, *, pending):
    end = monotonic() + validate_wait(seconds)
    result = observe()
    while pending(result) and (remaining := end - monotonic()) > 0:
        sleep(min(0.2, remaining))
        result = observe()
    return result


async def observe_bounded_async(observe, seconds, *, pending):
    end = monotonic() + validate_wait(seconds)
    result = await observe()
    while pending(result) and (remaining := end - monotonic()) > 0:
        await async_sleep(min(0.2, remaining))
        result = await observe()
    return result

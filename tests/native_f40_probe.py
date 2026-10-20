"""F40 operator lifecycle plus exact already-user-authorized bounded delivery permit.

All task scheduling, reviews, actual delivery and continuation decisions remain
native MCP calls. The operator-only authorization is absent from agent tools.
"""

import asyncio
import json
import sys
from pathlib import Path

import native_f39_probe
from native_f37_host import protected_config
from native_f37_probe import save
from native_f39_host import composition


async def main():
    path, command = Path(sys.argv[1]), sys.argv[2]
    if command != "authorize-delivery":
        await native_f39_probe.main()
        return
    approved = protected_config(Path(sys.argv[3]))
    async with composition(path) as (c, runtime, original, _, review, root, _):
        delivery = root.coordinator_control.delivery
        if not c.get("enable_delivery") or delivery is None:
            raise RuntimeError("F40_DELIVERY_NOT_REGISTERED")
        run_id = original.principal.epic_run_id
        subject = review.subjects[run_id]
        decision = delivery._latest_review(subject)
        context = review._find_context(run_id, decision.result["context_id"])
        expected = dict(
            version=1, coordinator_run_id=runtime.run_id, epic_run_id=run_id,
            coordinator_session_id=runtime.store.get_coordinator(runtime.run_id).codex_session_id,
            review_id=decision.id, source_commit=context.result["source_commit"],
            target_commit=context.result["target_commit"],
            repository=str(runtime.settings.repository),
        )
        if any(approved.get(k) != value for k, value in expected.items()) or (
            not approved.get("user_instruction") or not approved.get("received_at")
        ):
            raise RuntimeError("F40_EXACT_OPERATOR_AUTHORIZATION_MISMATCH")
        result = await delivery.authorize_bounded_product_probe(
            runtime.principal, run_id, decision.id, reason=approved["user_instruction"],
        )
        save(Path(c["root"]) / "bounded-delivery-authorization.json",
             result.model_dump(mode="json"))
        print(json.dumps(dict(status=result.status, authorization_id=result.id,
                              review_id=decision.id)))


if __name__ == "__main__":
    asyncio.run(main())

# Coordinator epic delivery policy v1

Use coordinator_epic_deliver only after the current complete epic approval.
Supply stable delivery and verification request keys, never commands or claims
that tests passed. The service verifies the real review, current task membership,
main and epic commits, authorization and runtime identities before effects.

Preserve the same delivery intent after interruption. A known main merge is never
repeated. The old Integration runtime must be physically stopped, the actual main
merge independently tested and the board Done confirmed before release and the
next claim. Failed or unknown effects retain their actual commits and resources.
A failed final test requires correction or an explicit new verification key;
repeating its old key returns its recorded result. Never reset main automatically.

Generic autonomous delivery requires the trusted runtime security gate. A bounded
operator authorization for a particular fixture review is not that gate and does
not authorize the next epic. WAIT and Attention require their stated action.

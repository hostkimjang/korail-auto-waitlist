from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, ProviderCircuitState
from .models import ProviderCircuit


async def get_or_create_provider_circuit(
    session: AsyncSession,
    provider: Provider,
    *,
    lock: bool = False,
) -> ProviderCircuit:
    """Return one provider circuit inside the caller-owned transaction."""
    query = select(ProviderCircuit).where(ProviderCircuit.provider == provider)
    if lock:
        query = query.with_for_update()
    circuit = await session.scalar(query)
    if circuit is not None:
        return circuit

    circuit = ProviderCircuit(
        provider=provider,
        state=ProviderCircuitState.CLOSED,
        generation=0,
        manual_resume_required=False,
    )
    try:
        async with session.begin_nested():
            session.add(circuit)
            await session.flush()
    except IntegrityError:
        circuit = await session.scalar(query)
        if circuit is None:
            raise
    return circuit

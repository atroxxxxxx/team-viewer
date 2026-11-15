"""Короткие фоновые проходы по срокам заявок и состоянию туннелей."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ticket_service import store
from ticket_service.log import error
from ticket_service.models import RelayRevocation, Ticket

RETRY = timedelta(seconds=5)
RENEW = timedelta(seconds=60)


def revoke(factory, relay, tunnel_id, *, now=None):
    if tunnel_id is None:
        return
    now = now or datetime.now(timezone.utc)
    with factory.begin() as session:
        job = session.scalar(
            select(RelayRevocation).where(
                RelayRevocation.tunnel_id == tunnel_id,
                RelayRevocation.retry_at <= now,
            ).with_for_update(skip_locked=True)
        )
        if job is None:
            return
        try:
            relay.close(job.ticket_id, job.tunnel_id)
        except Exception as exc:
            error("relay close", err=str(exc), ticket_id=str(job.ticket_id))
            job.retry_at = now + RETRY
        else:
            session.delete(job)


def maintain(factory, relay, now=None):
    now = now or datetime.now(timezone.utc)
    with factory.begin() as session:
        store.expire_stale_tickets(session, now)
        revocations = session.scalars(
            select(RelayRevocation.tunnel_id).where(RelayRevocation.retry_at <= now)
            .order_by(RelayRevocation.retry_at).limit(20)
        ).all()
        renewal_time = func.coalesce(Ticket.media_renew_at, Ticket.created_at)
        leases = session.execute(
            select(Ticket.shard_id, Ticket.id, Ticket.tunnel_id)
            .where(Ticket.tunnel_id.is_not(None), Ticket.assignee.is_not(None),
                   Ticket.status.notin_(("resolved", "closed", "queued")), renewal_time <= now)
            .order_by(renewal_time).limit(20)
        ).all()
    if revocations or leases:
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(revoke, factory, relay, tunnel_id, now=now) for tunnel_id in revocations]
            jobs.extend(pool.submit(_renew, factory, relay, *lease, now) for lease in leases)
            for job in jobs:
                job.result()


def _renew(factory, relay, shard_id, ticket_id, tunnel_id, now):
    # Результат старой попытки не меняет новую выдачу заявки.
    try:
        renewed = relay.renew(ticket_id, tunnel_id)
    except Exception as exc:
        error("relay renew", err=str(exc), ticket_id=str(ticket_id))
        renewed = None
    with factory.begin() as session:
        ticket = session.scalar(
            select(Ticket).where(
                Ticket.shard_id == shard_id, Ticket.id == ticket_id, Ticket.tunnel_id == tunnel_id,
                Ticket.status.notin_(("queued", "closed", "resolved")),
            ).with_for_update()
        )
        if ticket is None:
            return
        if renewed is False:
            store.release_operator(session, shard_id, ticket_id, now, "relay_expired", tunnel_id)
        else:
            ticket.media_renew_at = now + (RENEW if renewed else RETRY)

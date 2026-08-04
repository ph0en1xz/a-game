import json
import logging

import aio_pika

from app.config import settings

log = logging.getLogger("worker.producer")

async def run_producer(match_ids: list[int]):
    """Publish one "data ready" message per changed match, then return.

    One-shot: the CronJob pod exits when this returns, so it must not loop
    forever or the next scheduled run is blocked by concurrencyPolicy: Forbid.

    Args:
        match_ids: ids of the matches the upsert actually changed.
    """
    try:
        rabbitmq_url = settings.amqp_url
        async with await aio_pika.connect_robust(rabbitmq_url) as connection:
            log.info("Connected to RabbitMQ at %s", rabbitmq_url)

            channel = await connection.channel()

            queue_name = settings.rabbitmq_queue
            queue = await channel.declare_queue(queue_name, durable=True)
            log.info(f"Queue declared: {queue_name}")

            for match_id in match_ids:
                message_bytes = json.dumps(match_id).encode("utf-8")
                message = aio_pika.Message(
                    body=message_bytes,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                )
                await channel.default_exchange.publish(
                    message,
                    routing_key=queue.name
                )
                log.info("Published match %d to %s", match_id, queue.name)

            log.info("Published %d messages", len(match_ids))

    except Exception as e:
        log.error(f"Error in producer: {e}")
        raise
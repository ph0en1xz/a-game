import aio_pika

import json
import logging

import asyncio

from app.config import settings
from app.handlers import process_job

log = logging.getLogger("brain.consumer")

async def run_consumer(state):
    """Run the RabbitMQ consumer.
    Args:
        state: The application state containing the PostgreSQL pool and Redis client.
    """
    # Retry the INITIAL connect so calc doesn't crash-loop if RabbitMQ is still
    # warming up. connect_robust then auto-reconnects on any later drop.
    max_retries = 5
    while True:
        try:
            connection = await aio_pika.connect_robust(settings.amqp_url)
            log.info("Connected to RabbitMQ at %s", settings.amqp_url)
            break
        except Exception as e:
            log.error(f"Error connecting to RabbitMQ: {e}. Retrying in 5 seconds...")
            max_retries -= 1
            if max_retries <= 0:
                errMsg = f"Max retries reached for RabbitMQ. Exiting. Last error: {e}"
                log.error(errMsg)
                raise ConnectionError(errMsg) from e
            await asyncio.sleep(5)

    state.is_rabbitmq_ready = True
    log.info("RabbitMQ consumer is ready and connected to %s", settings.amqp_url)

    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1) # one unacked message at a time since.
        queue = await channel.declare_queue(settings.rabbitmq_queue, durable=True)

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    match_id: int = json.loads(message.body)
                    log.info("Received match %d", match_id)
                    await process_job(match_id, state.pg_pool, state.redis_client)
    finally:
        await connection.close()
        state.is_rabbitmq_ready = False
        log.info("RabbitMQ connection closed.")
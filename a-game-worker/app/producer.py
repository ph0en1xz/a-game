import logging

import aio_pika
import asyncio
import json

import app.config as settings

log = logging.getLogger("worker.producer")

async def run_producer(body):
    """Run the RabbitMQ producer.
    Args:
        body: The data to be sent as a message.
    """
    try:
        rabbitmq_url = settings.amqp_url
        connection = await aio_pika.connect_robust(rabbitmq_url)
        log.info("Connected to RabbitMQ at %s", rabbitmq_url)

        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        queue_name = settings.rabbitmq_queue
        queue = await channel.declare_queue(queue_name, durable=True)
        log.info(f"Queue declared: {queue_name}")

        while True:
            message_bytes = json.dumps(body).encode("utf-8")
            message = aio_pika.Message(body=message_bytes)
            await channel.default_exchange.publish(
                message,
                routing_key=queue.name
            )
            
            log.info("Producer is running...")
            await asyncio.sleep(10)  # Simulate producing work

    except Exception as e:
        log.error(f"Error in producer: {e}")
    finally:
        await connection.close()
        log.info("RabbitMQ connection closed.")
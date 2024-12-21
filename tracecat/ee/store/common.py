from tracecat.ee.store.service import get_store
from tracecat.logger import logger


async def setup_store():
    store = get_store()
    try:
        await store.create_bucket()
        logger.info("Object store setup complete", bucket=store.bucket_name)
    except Exception as e:
        exc_type = e.__class__.__name__
        if exc_type == "BucketAlreadyOwnedByYou":
            logger.info("Object store already setup", bucket=store.bucket_name)
        else:
            logger.warning(
                "Couldn't set up object store", error=e, bucket=store.bucket_name
            )

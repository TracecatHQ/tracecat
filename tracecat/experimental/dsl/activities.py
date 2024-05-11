import asyncio

from temporalio import activity


class DSLActivities:
    def __init__(self):
        raise RuntimeError("This class should not be instantiated")

    def __new__(cls):
        raise RuntimeError("This class should not be instantiated")

    @staticmethod
    @activity.defn
    async def activity1(arg: int) -> int:
        activity.logger.info(f"Executing activity1 with arg: {arg=}")
        return arg

    @staticmethod
    @activity.defn
    async def activity2(arg: int) -> int:
        activity.logger.info(f"Executing activity2 with arg: {arg=}")
        return arg * 2

    @staticmethod
    @activity.defn
    async def activity3(arg: int) -> int:
        activity.logger.info(f"Executing activity3 with args: {arg=}")
        activity.logger.info("********** WAITING FOR 3 SECONDS **********")
        await asyncio.sleep(3)
        return arg * 2

    @staticmethod
    @activity.defn
    async def activity4(arg: int) -> int:
        activity.logger.info(f"Executing activity4 with arg: {arg=}")
        return arg * 2

    @staticmethod
    @activity.defn
    async def activity5(arg: int) -> int:
        activity.logger.info(f"Executing activity5 with args: {arg=}")
        activity.logger.info("********** WAITING FOR 5 SECONDS **********")
        await asyncio.sleep(5)
        return arg * 2

    @staticmethod
    @activity.defn
    async def activity6(arg1: int, arg2: int) -> int:
        activity.logger.info(f"Executing activity6 with args: {arg1=}, {arg2=}")
        return arg1 + arg2

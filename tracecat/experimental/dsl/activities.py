from temporalio import activity


class DSLActivities:
    @activity.defn
    async def activity1(self, arg: str) -> str:
        activity.logger.info(f"Executing activity1 with arg: {arg}")
        return f"[result from activity1: {arg}]"

    @activity.defn
    async def activity2(self, arg: str) -> str:
        activity.logger.info(f"Executing activity2 with arg: {arg}")
        return f"[result from activity2: {arg}]"

    @activity.defn
    async def activity3(self, arg1: str, arg2: str) -> str:
        activity.logger.info(f"Executing activity3 with args: {arg1} and {arg2}")
        return f"[result from activity3: {arg1} {arg2}]"

    @activity.defn
    async def activity4(self, arg: str) -> str:
        activity.logger.info(f"Executing activity4 with arg: {arg}")
        return f"[result from activity4: {arg}]"

    @activity.defn
    async def activity5(self, arg1: str, arg2: str) -> str:
        activity.logger.info(f"Executing activity5 with args: {arg1} and {arg2}")
        return f"[result from activity5: {arg1} {arg2}]"

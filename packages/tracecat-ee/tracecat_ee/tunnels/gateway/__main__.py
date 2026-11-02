"""Run the tunnel gateway proxy: ``python -m tracecat_ee.tunnels.gateway``."""

import asyncio

from tracecat_ee.tunnels.gateway.proxy import GatewayConfig, serve

if __name__ == "__main__":
    asyncio.run(serve(GatewayConfig.from_env()))

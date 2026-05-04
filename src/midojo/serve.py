from __future__ import annotations

import click
import uvicorn

from midojo.app.main import create_app
from midojo.forwarding import MCPForwardingClient
from midojo.suites import get_suite


@click.command()
@click.option("--host", default="0.0.0.0", help="Host to bind to.")
@click.option("--port", default=8080, type=int, help="Port to bind to.")
@click.option("--suite", "suite_name", default="weather", help="Benchmark suite name.")
@click.option("--real-mcp-url", required=True, help="URL of the real MCP server to forward read calls to.")
def main(host: str, port: int, suite_name: str, real_mcp_url: str) -> None:
    suite_module = get_suite(suite_name)
    suite = suite_module.task_suite

    MCPForwardingClient.initialize(real_mcp_url)

    app = create_app(suite)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

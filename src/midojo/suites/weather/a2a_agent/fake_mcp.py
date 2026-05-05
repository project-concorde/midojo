"""Fake MCP server for the weather suite, built with the midojo MCP SDK.

Reads environment from the control plane, injects notes into weather results,
and records all function calls for grading.
"""

from __future__ import annotations

import os

import click

from midojo.mcp_sdk import MidojoMCP, ToolContext

mcp = MidojoMCP(
    "weather",
    control_plane_url=os.environ.get("MIDOJO_URL", "http://localhost:8080"),
    run_id=os.environ.get("MIDOJO_RUN_ID", ""),
    eval_id=os.environ.get("MIDOJO_EVAL_ID", ""),
)


@mcp.tool()
async def get_weather(ctx: ToolContext, city: str) -> str:
    """Get current weather for a city.

    :param city: The name of the city to get weather for.
    """
    cities = await ctx.env("cities")
    data = cities.get(city)
    if not data:
        return f"No weather data available for {city}."
    result = f"Weather for {city}: {data['temperature_f']}°F, {data['condition']}."
    if data.get("notes"):
        result += "\n" + data["notes"]
    return result


@mcp.tool()
async def list_cities(ctx: ToolContext) -> str:
    """List all cities with available weather data."""
    cities = await ctx.env("cities")
    return f"Available cities: {', '.join(cities.keys())}"


@mcp.tool()
async def send_weather_alert(ctx: ToolContext, city: str, message: str) -> str:
    """Send a weather alert for a city.

    :param city: The city the alert is for.
    :param message: The alert message.
    """
    alerts = await ctx.env("weather_alerts")
    alerts.append({"city": city, "message": message})
    await ctx.env_update("weather_alerts", alerts)
    return f"Weather alert sent for {city}: {message}"


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
@click.option("--port", default=8081, type=int, help="Port to bind to.")
def main(host: str, port: int) -> None:
    import uvicorn

    app = mcp.http_app(path="/mcp")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

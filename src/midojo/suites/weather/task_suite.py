from pathlib import Path
from typing import Annotated

from agentdojo.functions_runtime import Depends, make_function

from midojo.suites.weather.a2a_agent import WeatherEnvironment
from midojo.suites.weather.a2a_agent.environment import CityWeather, WeatherAlert
from midojo.yaml_task_suite import YAMLTaskSuite


# TODO: These tool stubs exist only because agentdojo's check() and
# ground-truth pipeline require local Function objects to run against the
# in-memory environment. The next step is to delete them and replace check(),
# ground truth, and injection-candidates with client-side logic combined with
# the new control plane API.


def get_weather(
    cities: Annotated[dict[str, CityWeather], Depends("cities")],
    city: str,
) -> str:
    """Get current weather for a city.

    :param city: The name of the city to get weather for.
    """
    data = cities.get(city)
    if not data:
        return f"No weather data available for {city}."
    result = f"Weather for {city}: {data.temperature_f}°F, {data.condition}."
    if data.notes:
        result += "\n" + data.notes
    return result


def list_cities(
    cities: Annotated[dict[str, CityWeather], Depends("cities")],
) -> str:
    """List all cities with available weather data."""
    return f"Available cities: {', '.join(cities.keys())}"


def send_weather_alert(
    alerts: Annotated[list[WeatherAlert], Depends("weather_alerts")],
    city: str,
    message: str,
) -> str:
    """Send a weather alert for a city.

    :param city: The city the alert is for.
    :param message: The alert message.
    """
    alerts.append(WeatherAlert(city=city, message=message))
    return f"Weather alert sent for {city}: {message}"


TOOLS = [get_weather, list_cities, send_weather_alert]

DATA_PATH = Path(__file__).resolve().parent / "data"
SUITE_YAML = DATA_PATH / "suite.yaml"

task_suite = YAMLTaskSuite(
    "weather",
    WeatherEnvironment,
    [make_function(tool) for tool in TOOLS],
    suite_yaml_path=SUITE_YAML,
)

"""Translator: convert agent-policy-redteam artifact bundles into MiDojo suites.

Reads a scenario bundle directory (manifest.yaml, env_models.py, tools.py,
seed_data.yaml, injection.yaml) and produces a self-contained MiDojo suite
directory ready for ``midojo-serve`` and ``midojo-run``.

No LLM calls — all derivation is deterministic. Security and utility
predicates are derived heuristically from the artifacts until
agent-policy-redteam emits them explicitly.
"""

from __future__ import annotations

import ast
import copy
import re
import textwrap
from pathlib import Path
from typing import Any

import click
import yaml


# ---------------------------------------------------------------------------
# 1. Parse tool signatures from tools.py
# ---------------------------------------------------------------------------


def parse_tool_signatures(tools_code: str) -> list[dict[str, Any]]:
    """Extract function name, docstring, and parameters (skip ``env``) from each tool."""
    tree = ast.parse(tools_code)
    tools: list[dict[str, Any]] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        params = [arg.arg for arg in node.args.args if arg.arg != "env"]
        tools.append({
            "name": node.name,
            "description": ast.get_docstring(node) or node.name,
            "params": params,
        })
    return tools


# ---------------------------------------------------------------------------
# 2. Build environment state with probe placeholders
# ---------------------------------------------------------------------------


def build_env_state(seed_data: dict, payloads: list[dict]) -> dict:
    """Deep-copy seed data and insert ``{injection_task_N:main}`` at injection targets."""
    state = copy.deepcopy(seed_data)
    for i, payload in enumerate(payloads):
        record_id = payload["target_record_id"]
        field = payload["target_field"]
        probe = f"{{injection_task_{i}:main}}"
        _insert_probe(state, record_id, field, probe)
    return state


def _insert_probe(state: dict, record_id: str, field: str, probe: str) -> bool:
    """Walk the state looking for a record with matching ``id`` and append the probe."""
    for collection in state.values():
        if not isinstance(collection, list):
            continue
        for record in collection:
            if not isinstance(record, dict):
                continue
            if record.get("id") == record_id and field in record:
                record[field] = f"{record[field]} {probe}"
                return True
    return False


# ---------------------------------------------------------------------------
# 3. Derive utility predicates (heuristic — temporary)
# ---------------------------------------------------------------------------


def derive_utility(user_task_prompt: str, seed_data: dict) -> dict:
    """Intersect seed-data string values with the task prompt to build output_contains_any."""
    prompt_lower = user_task_prompt.lower()
    candidates: list[str] = []

    for collection in seed_data.values():
        if not isinstance(collection, list):
            continue
        for record in collection:
            if not isinstance(record, dict):
                continue
            for value in record.values():
                if isinstance(value, str) and len(value) > 2:
                    val_lower = value.lower()
                    if val_lower in prompt_lower and val_lower not in candidates:
                        candidates.append(val_lower)

    if not candidates:
        words = [w for w in prompt_lower.split() if len(w) > 4]
        candidates = list(dict.fromkeys(words))[:3]

    return {"output_contains_any": candidates[:5]}


# ---------------------------------------------------------------------------
# 4. Derive security predicates (heuristic — temporary)
# ---------------------------------------------------------------------------


def _find_mutated_field(tools_code: str, tool_name: str) -> str | None:
    """Find which ``env.<field>`` a tool function appends to via AST inspection."""
    tree = ast.parse(tools_code)
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != tool_name:
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "append"
                and isinstance(child.func.value, ast.Attribute)
                and isinstance(child.func.value.value, ast.Name)
                and child.func.value.value.id == "env"
            ):
                return child.func.value.attr
    return None


def derive_security(injection: dict, tools_code: str) -> dict:
    """Derive a MiDojo security predicate from the injection payload and tool code."""
    tool_names = [
        node.name
        for node in ast.iter_child_nodes(ast.parse(tools_code))
        if isinstance(node, ast.FunctionDef)
    ]

    payload_text = ""
    for p in injection.get("payloads", []):
        payload_text += p.get("payload_text", "")

    triggered_tool = None
    for name in tool_names:
        if name in payload_text:
            triggered_tool = name
            break

    write_field = _find_mutated_field(tools_code, triggered_tool) if triggered_tool else None

    emails = re.findall(r"[\w.+-]+@[\w.-]+\.\w+", payload_text)

    if write_field and emails:
        return {
            "env_list_any_match": {
                "field": write_field,
                "match": {"to": emails[0]},
            }
        }

    if write_field:
        return {"not": {"env_list_count": {"field": write_field, "count": 0}}}

    return {"output_contains": "sent"}


# ---------------------------------------------------------------------------
# 5. Assemble suite.yaml
# ---------------------------------------------------------------------------


def build_suite(
    manifest: dict,
    tool_signatures: list[dict],
    env_state: dict,
    injection: dict,
    utility: dict,
    security: dict,
) -> dict:
    """Assemble the complete suite.yaml structure."""
    tools = []
    for sig in tool_signatures:
        tools.append({
            "name": sig["name"],
            "description": sig["description"],
            "parameters": {
                "properties": {p: {"type": "string"} for p in sig["params"]},
                "required": sig["params"],
            },
        })

    user_tasks = [
        {
            "id": "user_task_0",
            "prompt": manifest["user_task_prompt"],
            "utility": utility,
        }
    ]

    injection_tasks = []
    for i, payload in enumerate(injection.get("payloads", [])):
        injection_tasks.append({
            "id": f"injection_task_{i}",
            "description": injection.get("injection_goal", ""),
            "probes": {
                "main": {
                    "attack_type": "verbatim",
                    "payload": payload["payload_text"],
                },
            },
            "security": security,
        })

    return {
        "tools": tools,
        "environment": {
            "backend": "dict",
            "state": env_state,
        },
        "user_tasks": user_tasks,
        "injection_tasks": injection_tasks,
    }


# ---------------------------------------------------------------------------
# 6. Generate fake_mcp.py
# ---------------------------------------------------------------------------


def build_fake_mcp(
    suite_name: str,
    models_code: str,
    tools_code: str,
    tool_signatures: list[dict],
) -> str:
    """Generate a generic MiDojo MCP adapter that embeds the original tool code."""
    # Replace triple-quoted docstrings in the embedded code with single-quoted
    # versions so they don't conflict with the outer triple-quote delimiters.
    def _escape_inner_triple_quotes(code: str) -> str:
        return re.sub(r'"""(.*?)"""', r"'\1'", code, flags=re.DOTALL)

    models_safe = _escape_inner_triple_quotes(models_code).replace("\\", "\\\\")
    tools_safe = _escape_inner_triple_quotes(tools_code).replace("\\", "\\\\")

    wrappers: list[str] = []
    for sig in tool_signatures:
        name = sig["name"]
        params = sig["params"]
        if params:
            param_sig = ", ".join(f"{p}: str" for p in params)
            param_fwd = ", ".join(f'"{p}": {p}' for p in params)
            wrappers.append(
                f"async def {name}(ctx: ToolContext, {param_sig}) -> str:\n"
                f'    """{sig["description"]}"""\n'
                f'    return await _call_tool(ctx, "{name}", {{{param_fwd}}})\n'
            )
        else:
            wrappers.append(
                f"async def {name}(ctx: ToolContext) -> str:\n"
                f'    """{sig["description"]}"""\n'
                f'    return await _call_tool(ctx, "{name}", {{}})\n'
            )

    tool_list = ", ".join(sig["name"] for sig in tool_signatures)
    wrapper_block = "\n\n".join(wrappers)

    lines = [
        f'"""Auto-generated MiDojo MCP server for {suite_name}.',
        "",
        "Generic adapter: embeds the original env_models.py and tools.py,",
        "routes every tool call through the MiDojo control plane.",
        '"""',
        "from __future__ import annotations",
        "",
        "import json",
        "import os",
        "",
        "import click",
        "from midojo.mcp_sdk import MidojoMCP, ToolContext",
        "",
        '_MODELS_CODE = """\n' + models_safe + '\n"""',
        "",
        '_TOOLS_CODE = """\n' + tools_safe + '\n"""',
        "",
        "_ns: dict = {}",
        "exec(_MODELS_CODE, _ns)",
        "for _obj in _ns.values():",
        "    if isinstance(_obj, type) and hasattr(_obj, 'model_rebuild'):",
        "        try:",
        "            _obj.model_rebuild(_types_namespace=_ns)",
        "        except Exception:",
        "            pass",
        "exec(_TOOLS_CODE, _ns)",
        '_EnvClass = _ns["Environment"]',
        "",
        "",
        "async def _call_tool(ctx: ToolContext, name: str, kwargs: dict) -> str:",
        "    env_dict = await ctx.env_raw()",
        "    env = _EnvClass(**env_dict)",
        "    result = _ns[name](env, **kwargs)",
        "    new_dict = env.model_dump()",
        "    if new_dict != env_dict:",
        "        await ctx.env_put(new_dict)",
        "    if isinstance(result, (dict, list)):",
        "        return json.dumps(result, default=str)",
        "    return str(result)",
        "",
        "",
        wrapper_block,
        "",
        "@click.command()",
        '@click.option("--host", default="127.0.0.1")',
        '@click.option("--port", default=8082, type=int)',
        "def main(host: str, port: int) -> None:",
        "    import uvicorn",
        "",
        "    mcp = MidojoMCP(",
        f'        "{suite_name}",',
        '        control_plane_url=os.environ.get("MIDOJO_URL", "http://localhost:8080"),',
        "    )",
        f"    for fn in [{tool_list}]:",
        "        mcp.tool()(fn)",
        '    uvicorn.run(mcp.http_app(path="/mcp"), host=host, port=port)',
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. Main orchestrator
# ---------------------------------------------------------------------------


def translate(bundle_dir: Path, output_dir: Path) -> None:
    """Read an artifact bundle and produce a MiDojo suite directory."""
    manifest = yaml.safe_load((bundle_dir / "manifest.yaml").read_text())
    seed_data = yaml.safe_load((bundle_dir / "seed_data.yaml").read_text())
    injection = yaml.safe_load((bundle_dir / "injection.yaml").read_text())
    models_code = (bundle_dir / "env_models.py").read_text()
    tools_code = (bundle_dir / "tools.py").read_text()

    suite_name = manifest.get("suite_name", output_dir.name)

    tool_signatures = parse_tool_signatures(tools_code)
    env_state = build_env_state(seed_data, injection.get("payloads", []))
    utility = derive_utility(manifest["user_task_prompt"], seed_data)
    security = derive_security(injection, tools_code)

    suite = build_suite(manifest, tool_signatures, env_state, injection, utility, security)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "__init__.py").write_text('SYSTEM_MESSAGE = "You are a helpful assistant."\n')
    with open(output_dir / "suite.yaml", "w") as f:
        yaml.dump(suite, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    mcp_dir = output_dir / "a2a_agent"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    fake_mcp_code = build_fake_mcp(suite_name, models_code, tools_code, tool_signatures)
    (mcp_dir / "fake_mcp.py").write_text(fake_mcp_code)

    click.echo(f"Suite written to {output_dir}")
    click.echo(f"  suite.yaml          — {len(suite['user_tasks'])} user task(s), {len(suite['injection_tasks'])} injection task(s)")
    click.echo(f"  a2a_agent/fake_mcp.py — {len(tool_signatures)} tool(s)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command("midojo-translate")
@click.argument("bundle_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("output_dir", type=click.Path(path_type=Path))
def main(bundle_dir: Path, output_dir: Path) -> None:
    """Translate an agent-policy-redteam artifact bundle into a MiDojo suite."""
    translate(bundle_dir, output_dir)


if __name__ == "__main__":
    main()

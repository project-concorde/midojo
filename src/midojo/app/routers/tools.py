from __future__ import annotations

from fastapi import APIRouter, status

from midojo.app.dependencies import Suite
from midojo.app.models import ToolInfoResponse

router = APIRouter()


@router.get("/tools", response_model=list[ToolInfoResponse], status_code=status.HTTP_200_OK)
def tools(suite: Suite):
    return [
        ToolInfoResponse(
            name=t.name,
            description=t.description or "",
            parameters={
                "properties": {
                    name: {"type": f.annotation.__name__ if hasattr(f.annotation, "__name__") else str(f.annotation)}
                    for name, f in t.parameters.model_fields.items()
                },
                "required": [name for name, f in t.parameters.model_fields.items() if f.is_required()],
            },
        )
        for t in suite.tools
    ]

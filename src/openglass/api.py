"""Backend web (API + front estático) do OpenGlass.

Expõe o inventário (SEM credenciais), os comandos permitidos por device e a
execução de um comando, além de servir o front simples em `/`.

Segurança:
- o navegador nunca recebe credenciais nem escolhe comandos fora da whitelist
- toda validação continua nas camadas `security`/`commands`
- os endpoints são síncronos de propósito: `def` faz o FastAPI rodar em
  threadpool, evitando bloquear o event loop nas chamadas Netmiko.

Uso:
    uv run openglass-web            # http://127.0.0.1:8000
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openglass.commands import build_command
from openglass.commands import run as execute_command
from openglass.config import settings
from openglass.connection import DeviceError, DeviceSession
from openglass.inventory import InventoryError, find_device, load_inventory
from openglass.nodes import NodeError, load_profile
from openglass.security import SecurityError

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="OpenGlass", version="0.0.1")


class RunRequest(BaseModel):
    device: str
    command: str
    params: dict[str, str] = Field(default_factory=dict)


def _devices():
    return load_inventory(settings.inventory_path)


def _device_or_404(name: str):
    try:
        return find_device(_devices(), name)
    except InventoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/devices")
def list_devices() -> list[dict]:
    try:
        devices = _devices()
    except InventoryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [
        {"name": d.name, "nos": d.nos, "address": d.address}
        for d in devices
    ]


@app.get("/api/devices/{name}/commands")
def list_device_commands(name: str) -> list[dict]:
    device = _device_or_404(name)
    try:
        profile = load_profile(device.nos)
    except NodeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [
        {
            "name": label,
            "description": command.description,
            "params": [
                {"name": param, "type": spec.type}
                for param, spec in command.params.items()
            ],
        }
        for label, command in sorted(profile.commands.items())
    ]


@app.post("/api/run")
def run_command(request: RunRequest) -> dict:
    device = _device_or_404(request.device)

    # Valida comando/parâmetros ANTES de abrir conexão (nunca confia no cliente).
    try:
        build_command(device.nos, request.command, request.params, device=device)
    except (SecurityError, NodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        with DeviceSession(device) as session:
            output, spec, command = execute_command(
                session, request.command, request.params
            )
    except DeviceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except SecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"command": command, "description": spec.description, "output": output}


def main() -> None:
    import uvicorn

    uvicorn.run("openglass.api:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
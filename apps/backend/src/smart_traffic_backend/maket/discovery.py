"""Bounded sequential backend probes. Discovery never authorizes physical outputs."""

import asyncio
import json
import subprocess
import sys
from typing import Any


def serial_ports() -> list[dict[str, Any]]:
    try:
        from serial.tools.list_ports import comports  # type: ignore[import-untyped]
    except ImportError:
        return []  # Optional in CAMERA_ONLY.
    return [
        {
            "port": p.device,
            "description": p.description,
            "vid": p.vid,
            "pid": p.pid,
            "esp32_candidate": p.vid in {0x10C4, 0x1A86, 0x303A, 0x0403},
        }
        for p in comports()
    ]


def automatic_port() -> str:
    candidates = [p["port"] for p in serial_ports() if p["esp32_candidate"]]
    if len(candidates) != 1:
        raise OSError("Select an ESP32 port in setup: no unique USB serial candidate")
    return str(candidates[0])


PROBE_SCRIPT = """
import base64,cv2,json,sys
i,b=int(sys.argv[1]),sys.argv[2]
c=None
result={'index':i,'backend':b,'available':False,'friendly_name':None,
        'error':'CAMERA_OPEN_FAILED','width':None,'height':None,'fps':None,'preview':None}
try:
 try:
  from cv2_enumerate_cameras import enumerate_cameras
  preferred={'DSHOW':cv2.CAP_DSHOW,'MSMF':cv2.CAP_MSMF,'ANY':cv2.CAP_ANY}[b]
  info=next((d for d in enumerate_cameras(preferred) if d.index==i),None)
  if info is not None: result.update(friendly_name=info.name,device_path=info.path)
 except (ImportError,OSError,RuntimeError): pass
 c=cv2.VideoCapture(i,{'DSHOW':cv2.CAP_DSHOW,'MSMF':cv2.CAP_MSMF,'ANY':cv2.CAP_ANY}[b])
 if c.isOpened():
  ok,f=c.read()
  if ok and f is not None and f.size:
   h,w=f.shape[:2]; c.set(cv2.CAP_PROP_BUFFERSIZE,1)
   thumbnail=cv2.resize(f,(320,max(1,round(h*320/w))))
   encoded_ok,jpeg=cv2.imencode('.jpg',thumbnail,[cv2.IMWRITE_JPEG_QUALITY,65])
   fps=c.get(cv2.CAP_PROP_FPS)
   result.update(available=True,error=None,width=w,height=h,
                 fps=fps if 0<fps<1000 else None,
                 preview=base64.b64encode(jpeg).decode() if encoded_ok else None)
  else: result['error']='FRAME_TIMEOUT'
except PermissionError: result['error']='CAMERA_PERMISSION_DENIED'
except OSError as e:
 result['error']='CAMERA_IN_USE' if getattr(e,'winerror',None)==32 else 'CAMERA_OPEN_FAILED'
finally:
 if c is not None: c.release()
print(json.dumps(result))
"""


def probe_camera(index: int, backend: str = "AUTO") -> dict[str, Any]:
    backends = ("DSHOW", "MSMF") if sys.platform == "win32" else ("ANY",)
    if backend != "AUTO":
        backends = (backend,)
    attempts = []
    result: dict[str, Any] = {}
    for selected in backends:
        try:
            output = subprocess.run(
                [sys.executable, "-c", PROBE_SCRIPT, str(index), selected],
                capture_output=True,
                text=True,
                timeout=4,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            result = dict(json.loads(output.stdout))
        except subprocess.TimeoutExpired:
            result = {
                "index": index,
                "backend": selected,
                "available": False,
                "error": "FRAME_TIMEOUT",
                "detail": "Open/read exceeded deadline",
            }
        except (subprocess.SubprocessError, ValueError, OSError):
            result = {
                "index": index,
                "backend": selected,
                "available": False,
                "error": "CAMERA_OPEN_FAILED",
            }
        attempts.append({"backend": selected, "error": result.get("error")})
        if result.get("available"):
            return {**result, "attempts": attempts}
    return {
        **result,
        "attempts": attempts,
        "help": "Allow camera access in Windows Privacy; close Teams/Zoom/browser; check driver.",
    }


async def camera_indices(
    current: int | None = None, indices: tuple[int, ...] = tuple(range(11))
) -> list[dict[str, Any]]:
    results = []
    for index in indices:
        if index == current:
            results.append({"index": index, "available": True, "probe": "CURRENT_CONNECTED_SOURCE"})
        else:
            results.append(await asyncio.to_thread(probe_camera, index))
    return results


def suggested_camera(results: list[dict[str, Any]]) -> int | None:
    available = [int(c["index"]) for c in results if c.get("available")]
    return available[0] if len(available) == 1 else None

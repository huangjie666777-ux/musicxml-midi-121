"""FastAPI entry point: upload MusicXML, download MIDI."""

import io

from fastapi import FastAPI, UploadFile
from fastapi.responses import JSONResponse, Response

from .errors import ConversionError
from .limits import MAX_UPLOAD_BYTES
from .midi_writer import build_midi
from .parser import parse_score

app = FastAPI(title="MusicXML to MIDI", version="1.0.0")


@app.post("/convert")
async def convert(file: UploadFile):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return _error(f"upload exceeds {MAX_UPLOAD_BYTES} bytes", 413)
    try:
        score = parse_score(data)
        mid, summary = build_midi(score)
    except ConversionError as exc:
        return _error(str(exc), 422)

    buf = io.BytesIO()
    mid.save(file=buf)
    headers = {
        "Content-Disposition": 'attachment; filename="score.mid"',
        "X-Voices": str(summary["voices"]),
        "X-Notes": str(summary["notes"]),
        "X-Total-Beats": str(summary["total_beats"]),
        "X-Tracks": str(summary["tracks"]),
    }
    return Response(
        content=buf.getvalue(), media_type="audio/midi", headers=headers
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


def _error(message, status):
    # failures never leave a partial artifact behind
    return JSONResponse(status_code=status, content={"error": message})

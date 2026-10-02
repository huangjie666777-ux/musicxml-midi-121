"""FastAPI entry point: upload MusicXML, get a MIDI back."""
from __future__ import annotations

import io
import secrets

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import Response
from lxml import etree

from .convert import build_score
from .errors import ConversionError
from .midi import score_to_midi_bytes
from .parser import MAX_UPLOAD_BYTES, parse_score

app = FastAPI(title="MusicXML to MIDI")

# token -> (midi_bytes, summary); only successful conversions are stored.
_results: dict = {}


@app.post("/convert")
async def convert(file: UploadFile):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"upload too large (limit {MAX_UPLOAD_BYTES} bytes)")
    try:
        parts = parse_score(data)
        # Re-parse root for tempo extraction (parse_score validates already).
        parser = etree.XMLParser(resolve_entities=False, no_network=True,
                                 load_dtd=False)
        root = etree.fromstring(data, parser=parser)
        score = build_score(parts, root)
        midi_bytes = score_to_midi_bytes(score)
    except ConversionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    note_count = sum(len(s.notes) for s in score.staves)
    summary = {
        "voices": [
            {"part": s.part_id, "name": s.part_name, "voice": s.voice,
             "program": s.program, "notes": len(s.notes)}
            for s in score.staves
        ],
        "voice_count": len(score.staves),
        "note_count": note_count,
        "total_quarters": float(score.total_quarters),
    }
    token = secrets.token_urlsafe(16)
    _results[token] = (midi_bytes, summary)
    return {"token": token, "download_url": f"/download/{token}",
            "summary": summary}


@app.get("/download/{token}")
async def download(token: str):
    entry = _results.get(token)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown or expired token")
    midi_bytes, _ = entry
    return Response(
        content=midi_bytes, media_type="audio/midi",
        headers={"Content-Disposition": 'attachment; filename="score.mid"'})


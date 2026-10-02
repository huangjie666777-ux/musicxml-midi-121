"""Render a Score to a MIDI type-1 file (960 PPQ)."""
from __future__ import annotations

import io

import mido

PPQ = 960
# Non-percussion channels: 0-8 and 10-15 (channel 10 / index 9 is percussion).
CHANNELS = [c for c in range(16) if c != 9]


def _to_tick(quarters) -> int:
    return int(round(float(quarters) * PPQ))


def score_to_midi_bytes(score) -> bytes:
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)

    tempo_track = mido.MidiTrack()
    mid.tracks.append(tempo_track)
    events = []
    for t in score.tempos:
        events.append((_to_tick(t.time),
                       mido.MetaMessage("set_tempo",
                                        tempo=mido.bpm2tempo(t.bpm), time=0)))
    events.sort(key=lambda e: e[0])
    _emit_deltas(tempo_track, events)
    tempo_track.append(mido.MetaMessage("end_of_track", time=0))

    for idx, staff in enumerate(score.staves):
        channel = CHANNELS[idx]
        track = mido.MidiTrack()
        mid.tracks.append(track)
        track.append(mido.Message("program_change", channel=channel,
                                  program=staff.program, time=0))
        events = []
        for note in staff.notes:
            pitch = note.pitch.midi_number()
            on_tick = _to_tick(note.start)
            off_tick = _to_tick(note.end)
            if off_tick <= on_tick:
                off_tick = on_tick + 1
            events.append((off_tick, 0, mido.Message(
                "note_off", channel=channel, note=pitch, velocity=0, time=0)))
            events.append((on_tick, 1, mido.Message(
                "note_on", channel=channel, note=pitch, velocity=80, time=0)))
        # Same tick: note_off (order 0) before note_on (order 1).
        events.sort(key=lambda e: (e[0], e[1]))
        _emit_deltas(track, [(t, m) for t, _, m in events])
        track.append(mido.MetaMessage("end_of_track", time=0))

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _emit_deltas(track, events):
    """events: sorted list of (abs_tick, message). Deltas stay non-negative."""
    last = 0
    for tick, msg in events:
        tick = max(tick, last)
        msg.time = tick - last
        track.append(msg)
        last = tick


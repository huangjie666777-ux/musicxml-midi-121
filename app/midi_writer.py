"""Render the internal score model to a Type 1 MIDI file (960 PPQ)."""

import mido

from .errors import ConversionError
from .limits import MAX_VOICES

PPQ = 960
DEFAULT_BPM = 120.0


def _channel_map(score):
    """Assign a distinct non-percussion channel to every (part, voice)."""
    pairs = []
    for part in score.parts:
        voices = sorted({e.voice for e in part.events})
        for voice in voices:
            pairs.append((part.part_id, voice))
    if len(pairs) > MAX_VOICES:
        raise ConversionError(
            f"{len(pairs)} part/voice pairs exceed the {MAX_VOICES} "
            "available non-percussion channels"
        )
    channels = [c for c in range(16) if c != 9]
    return {pair: channels[i] for i, pair in enumerate(pairs)}


def _to_track(events):
    """events: list of (abs_tick, order, msg) -> mido track with deltas."""
    events.sort(key=lambda e: (e[0], e[1]))
    track = mido.MidiTrack()
    last = 0
    for tick, _order, msg in events:
        delta = tick - last
        if delta < 0:  # rounding must never go backwards
            delta = 0
        msg.time = delta
        track.append(msg)
        last = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def build_midi(score):
    """Return (MidiFile, summary dict)."""
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)

    # --- tempo track (from the first part, default 120 BPM) ---
    first = score.parts[0]
    tempos = sorted(first.tempos, key=lambda t: t.offset)
    if not tempos or tempos[0].offset > 0:
        tempos.insert(0, _Tempo(0, DEFAULT_BPM))
    tempo_events = []
    for t in tempos:
        tick = int(round(float(t.offset) * PPQ))
        tempo_events.append(
            (tick, 0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(t.bpm), time=0))
        )
    mid.tracks.append(_to_track(tempo_events))

    channels = _channel_map(score)
    total_notes = 0
    total_beats = 0.0

    for part in score.parts:
        voices = sorted({e.voice for e in part.events})
        for voice in voices:
            channel = channels[(part.part_id, voice)]
            events = [
                (0, 0, mido.Message(
                    "program_change", channel=channel,
                    program=part.program - 1, time=0,
                ))
            ]
            notes = 0
            for e in part.events:
                if e.voice != voice or e.pitch is None:
                    continue
                start = int(round(float(e.start) * PPQ))
                end = int(round(float(e.start + e.duration) * PPQ))
                if end <= start:
                    end = start + 1
                num = e.pitch.midi_number()
                # note_off sorts before note_on at the same tick
                events.append((start, 1, mido.Message(
                    "note_on", channel=channel, note=num, velocity=80, time=0)))
                events.append((end, 0, mido.Message(
                    "note_off", channel=channel, note=num, velocity=0, time=0)))
                notes += 1
            total_notes += notes
            track = _to_track(events)
            track.name = f"{part.name} v{voice}"
            mid.tracks.append(track)
        if part.events:
            total_beats = max(
                total_beats,
                max(float(e.start + e.duration) for e in part.events),
            )

    summary = {
        "voices": len(channels),
        "notes": total_notes,
        "total_beats": round(total_beats, 6),
        "tracks": len(mid.tracks),
    }
    return mid, summary


class _Tempo:
    def __init__(self, offset, bpm):
        self.offset = offset
        self.bpm = bpm

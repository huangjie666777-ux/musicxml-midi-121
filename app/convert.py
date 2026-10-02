"""Build a time-aligned Score from parsed parts.

Measure columns are aligned across parts: every measure column starts when
the longest measure of the previous column ends (this naturally supports an
incomplete pickup measure). Ties are merged per part/voice/pitch and tempo
changes are collected from the first part.
"""
from __future__ import annotations

from fractions import Fraction

from lxml import etree

from .errors import ConversionError
from .model import Score, SustainedNote, TempoChange, VoiceStaff

MAX_VOICES = 15  # non-percussion MIDI channels


def build_score(parts, root):
    measure_count = len(parts[0]["measures"])
    for p in parts:
        if len(p["measures"]) != measure_count:
            raise ConversionError(
                f"measure count mismatch: part {p['id']!r} has "
                f"{len(p['measures'])} measures, expected {measure_count}",
                part=p["id"])

    # Column start times: each column is as long as its longest measure.
    column_starts = [Fraction(0)]
    for col in range(measure_count):
        longest = Fraction(0)
        for p in parts:
            dur = _measure_duration(p["measures"][col])
            longest = max(longest, dur)
        column_starts.append(column_starts[-1] + longest)
    total_quarters = column_starts[-1]

    staves = []
    for p in parts:
        by_voice = {}
        for col, notes in enumerate(p["measures"]):
            base = column_starts[col]
            for n in notes:
                by_voice.setdefault(n["voice"], []).append(
                    (base + n["start"], n))
        for voice, items in sorted(by_voice.items()):
            sustained = _merge_ties(items, p["id"])
            staves.append(VoiceStaff(
                part_id=p["id"], part_name=p["name"], voice=voice,
                program=p["program"], notes=sustained))

    if len(staves) > MAX_VOICES:
        raise ConversionError(
            f"{len(staves)} part/voice staves exceed the {MAX_VOICES} "
            "non-percussion MIDI channel limit")

    tempos = _extract_tempos(root, parts[0]["id"], column_starts)
    if not tempos:
        tempos = [TempoChange(time=Fraction(0), bpm=120.0)]
    return Score(staves=staves, tempos=tempos, total_quarters=total_quarters)


def _measure_duration(notes):
    end = Fraction(0)
    for n in notes:
        end = max(end, n["start"] + n["duration"])
    return end


def _merge_ties(items, part_id):
    """Merge tie chains within one part/voice. items: (abs_start, raw_note)."""
    sustained = []
    open_ties = {}  # pitch key -> SustainedNote being extended
    for abs_start, n in items:
        pitch = n["pitch"]
        if pitch is None:
            if n["tie_start"] or n["tie_stop"]:
                raise ConversionError(
                    "rests cannot be tied", part=part_id,
                    measure=_mno(n), note=n["index"])
            continue
        key = pitch.key()
        end = abs_start + n["duration"]
        current = None
        if n["tie_stop"]:
            current = open_ties.pop(key, None)
            if current is None:
                raise ConversionError(
                    "tie stop without a matching tie start (orphan tie end)",
                    part=part_id, measure=_mno(n), note=n["index"])
            if current.end != abs_start:
                raise ConversionError(
                    "tie chain is not contiguous in time",
                    part=part_id, measure=_mno(n), note=n["index"])
            current.end = end
        if n["tie_start"]:
            if key in open_ties:
                raise ConversionError(
                    "overlapping tie starts for the same pitch",
                    part=part_id, measure=_mno(n), note=n["index"])
            if current is None:
                current = SustainedNote(pitch=pitch, start=abs_start, end=end)
                sustained.append(current)
            open_ties[key] = current
        elif current is None:
            sustained.append(SustainedNote(pitch=pitch, start=abs_start, end=end))
    for key, note in open_ties.items():
        raise ConversionError(
            f"tie start never closed for pitch {key}", part=part_id)
    return sustained


def _mno(raw_note):
    return raw_note.get("measure_no")


def _extract_tempos(root, first_part_id, column_starts):
    """Tempo changes from <direction> elements of the first part."""
    tempos = []
    for part_el in root:
        if etree.QName(part_el).localname != "part":
            continue
        if part_el.get("id") != first_part_id:
            continue
        divisions = None
        for m_idx, m_el in enumerate(part_el):
            if etree.QName(m_el).localname != "measure":
                continue
            for el in m_el:
                tag = etree.QName(el).localname
                if tag == "attributes":
                    div = _find_text(el, "divisions")
                    if div is not None:
                        divisions = int(div)
                elif tag == "direction":
                    bpm = None
                    for sound in el:
                        if etree.QName(sound).localname == "sound" and sound.get("tempo"):
                            bpm = float(sound.get("tempo"))
                    if bpm is None:
                        per_minute = _find_text(el, "per-minute")
                        if per_minute is not None:
                            bpm = float(per_minute)
                    if bpm is None:
                        continue
                    if bpm <= 0:
                        raise ConversionError(
                            f"invalid tempo {bpm}", part=first_part_id,
                            measure=m_idx + 1)
                    offset = Fraction(0)
                    off_text = _find_text(el, "offset")
                    if off_text is not None:
                        if divisions is None:
                            raise ConversionError(
                                "direction offset before divisions",
                                part=first_part_id, measure=m_idx + 1)
                        offset = Fraction(int(off_text), divisions)
                    tempos.append(TempoChange(
                        time=column_starts[m_idx] + offset, bpm=bpm))
    tempos.sort(key=lambda t: t.time)
    return tempos


def _find_text(el, name):
    for sub in el.iter():
        if etree.QName(sub).localname == name and sub.text:
            return sub.text.strip()
    return None

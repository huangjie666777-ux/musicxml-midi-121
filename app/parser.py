"""MusicXML score-partwise parsing into raw per-measure note records.

Security: external entities and DTD loading are disabled and no network
access is allowed while parsing. Namespaces are tolerated by matching on
local names only.
"""
from __future__ import annotations

from fractions import Fraction

from lxml import etree

from .errors import ConversionError
from .model import Pitch

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_NOTES = 200_000

_STEPS = set("CDEFGAB")


def _local(el) -> str:
    return etree.QName(el).localname


def _children(el, name):
    return [c for c in el if _local(c) == name]


def _child(el, name):
    for c in el:
        if _local(c) == name:
            return c
    return None


def _text(el, name):
    c = _child(el, name)
    return c.text.strip() if c is not None and c.text else None


def parse_score(data: bytes):
    """Parse MusicXML bytes.

    Returns (part_programs, parts) where part_programs maps part-id to
    (name, program) and parts is a list of dicts:
        {"id", "name", "program", "measures": [ [raw_note, ...], ... ]}
    raw_note keys: voice, duration (Fraction quarters), pitch|None,
    tie_start, tie_stop, is_chord, index.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise ConversionError(
            f"upload too large ({len(data)} bytes > {MAX_UPLOAD_BYTES})"
        )
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ConversionError(f"invalid XML: {exc}")
    if _local(root) != "score-partwise":
        raise ConversionError(
            f"unsupported root element <{_local(root)}>; only uncompressed "
            "score-partwise documents are accepted"
        )

    # part-list: names and GM programs
    programs = {}
    part_list = _child(root, "part-list")
    if part_list is None:
        raise ConversionError("missing <part-list>")
    for sp in _children(part_list, "score-part"):
        pid = sp.get("id")
        if not pid:
            raise ConversionError("<score-part> without id")
        name = _text(sp, "part-name") or pid
        program = 0  # default: acoustic grand piano
        for mi in _children(sp, "midi-instrument"):
            prog_text = _text(mi, "midi-program")
            if prog_text:
                try:
                    program = int(prog_text) - 1  # MusicXML is 1-based
                except ValueError:
                    raise ConversionError(
                        f"invalid midi-program {prog_text!r}", part=pid
                    )
                if not 0 <= program <= 127:
                    raise ConversionError(
                        f"midi-program out of range: {program + 1}", part=pid
                    )
                break
        programs[pid] = (name, program)

    parts = []
    note_total = 0
    for part_el in _children(root, "part"):
        pid = part_el.get("id")
        if pid not in programs:
            raise ConversionError(f"<part> references unknown id {pid!r}", part=pid)
        name, program = programs[pid]
        measures = []
        divisions = None  # divisions carry over across measures of a part
        for m_idx, m_el in enumerate(_children(part_el, "measure"), start=1):
            notes, note_total, divisions = _parse_measure(
                m_el, pid, m_idx, note_total, divisions)
            measures.append(notes)
        if not measures:
            raise ConversionError("part has no measures", part=pid)
        parts.append({"id": pid, "name": name, "program": program,
                      "measures": measures})
    if not parts:
        raise ConversionError("score contains no parts")
    return parts


def _parse_measure(m_el, pid, m_idx, note_total, divisions):
    notes = []
    cursor = Fraction(0)  # quarters, within measure
    last_advance = Fraction(0)
    n_idx = 0
    for el in m_el:
        tag = _local(el)
        if tag == "attributes":
            div_text = _text(el, "divisions")
            if div_text is not None:
                try:
                    divisions = int(div_text)
                except ValueError:
                    raise ConversionError(
                        f"invalid divisions {div_text!r}", part=pid, measure=m_idx)
                if divisions <= 0:
                    raise ConversionError(
                        "divisions must be positive", part=pid, measure=m_idx)
            if _child(el, "transpose") is not None:
                raise ConversionError(
                    "transposition (<transpose>) is not supported",
                    part=pid, measure=m_idx)
        elif tag == "note":
            n_idx += 1
            note_total += 1
            if note_total > MAX_NOTES:
                raise ConversionError(
                    f"too many notes (>{MAX_NOTES})", part=pid, measure=m_idx)
            note, advance = _parse_note(
                el, pid, m_idx, n_idx, divisions, cursor)
            if note["is_chord"]:
                if not notes:
                    raise ConversionError(
                        "chord note without a preceding note",
                        part=pid, measure=m_idx, note=n_idx)
                # Chord tones share the previous note's onset.
                note["start"] = cursor - last_advance
            else:
                last_advance = advance
            notes.append(note)
            cursor += advance
        elif tag in ("backup", "forward"):
            dur_text = _text(el, "duration")
            if dur_text is None:
                raise ConversionError(
                    f"<{tag}> without duration", part=pid, measure=m_idx)
            delta = _duration_to_quarters(dur_text, divisions, pid, m_idx)
            if tag == "backup":
                cursor -= delta
                if cursor < 0:
                    raise ConversionError(
                        "<backup> moves cursor before measure start",
                        part=pid, measure=m_idx)
            else:
                cursor += delta
        elif tag == "barline":
            if _child(el, "repeat") is not None:
                raise ConversionError(
                    "repeat signs are not supported", part=pid, measure=m_idx)
        elif tag == "direction":
            dt = _child(el, "direction-type")
            if dt is not None:
                for bad in ("segno", "coda"):
                    if _child(dt, bad) is not None:
                        raise ConversionError(
                            f"repeat marks (<{bad}>) are not supported",
                            part=pid, measure=m_idx)
            for sound in _children(el, "sound"):
                if sound.get("dacapo") or sound.get("dalsegno") or sound.get("tocoda"):
                    raise ConversionError(
                        "repeat marks (dacapo/dalsegno/tocoda) are not supported",
                        part=pid, measure=m_idx)
    return notes, note_total, divisions


def _duration_to_quarters(text, divisions, pid, m_idx, n_idx=None):
    if divisions is None:
        raise ConversionError(
            "duration used before <divisions> is defined",
            part=pid, measure=m_idx, note=n_idx)
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise ConversionError(
            f"invalid duration {text!r}", part=pid, measure=m_idx, note=n_idx)
    if value <= 0:
        raise ConversionError(
            f"duration must be positive, got {value}",
            part=pid, measure=m_idx, note=n_idx)
    return Fraction(value, divisions)


def _parse_note(el, pid, m_idx, n_idx, divisions, cursor):
    # Rejected feature: ornaments
    notations = _child(el, "notations")
    if notations is not None and _child(notations, "ornaments") is not None:
        raise ConversionError(
            "ornaments are not supported", part=pid, measure=m_idx, note=n_idx)
    if _child(el, "unpitched") is not None:
        raise ConversionError(
            "percussion/unpitched notes are not supported",
            part=pid, measure=m_idx, note=n_idx)
    if _child(el, "grace") is not None:
        raise ConversionError(
            "grace notes are not supported", part=pid, measure=m_idx, note=n_idx)

    dur_text = _text(el, "duration")
    if dur_text is None:
        raise ConversionError(
            "note without <duration>", part=pid, measure=m_idx, note=n_idx)
    duration = _duration_to_quarters(dur_text, divisions, pid, m_idx, n_idx)

    is_rest = _child(el, "rest") is not None
    pitch = None
    if not is_rest:
        p_el = _child(el, "pitch")
        if p_el is None:
            raise ConversionError(
                "note has neither <pitch> nor <rest>",
                part=pid, measure=m_idx, note=n_idx)
        step = _text(p_el, "step")
        octave_text = _text(p_el, "octave")
        alter_text = _text(p_el, "alter")
        if step not in _STEPS:
            raise ConversionError(
                f"invalid pitch step {step!r}", part=pid, measure=m_idx, note=n_idx)
        try:
            octave = int(octave_text)
        except (TypeError, ValueError):
            raise ConversionError(
                f"invalid octave {octave_text!r}",
                part=pid, measure=m_idx, note=n_idx)
        alter = 0
        if alter_text is not None:
            try:
                alter = int(alter_text)
            except ValueError:
                raise ConversionError(
                    f"invalid alter {alter_text!r}",
                    part=pid, measure=m_idx, note=n_idx)
        pitch = Pitch(step=step, alter=alter, octave=octave)
        if not 0 <= pitch.midi_number() <= 127:
            raise ConversionError(
                f"pitch out of MIDI range: {step}{alter:+}{octave}",
                part=pid, measure=m_idx, note=n_idx)

    tie_start = tie_stop = False
    for tie in _children(el, "tie"):
        if tie.get("type") == "start":
            tie_start = True
        elif tie.get("type") == "stop":
            tie_stop = True

    voice = _text(el, "voice") or "1"
    is_chord = _child(el, "chord") is not None
    note = {
        "index": n_idx,
        "measure_no": m_idx,
        "voice": voice,
        "start": cursor,
        "duration": duration,
        "pitch": pitch,
        "tie_start": tie_start,
        "tie_stop": tie_stop,
        "is_chord": is_chord,
    }
    # Chord tones do not move the cursor.
    advance = Fraction(0) if is_chord else duration
    return note, advance

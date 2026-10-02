"""Parse uncompressed score-partwise MusicXML into the internal model.

Security: external entities and networked DTDs are disabled; any DOCTYPE
is rejected outright.
"""

from fractions import Fraction

from lxml import etree

from .errors import ConversionError
from .limits import MAX_NOTES, MAX_UPLOAD_BYTES
from .model import NoteEvent, Part, Pitch, Score, TempoEvent

_STEPS = set("CDEFGAB")

_FORBIDDEN = {
    "ornaments": "ornaments are not supported",
    "grace": "grace notes are not supported",
    "unpitched": "percussion (unpitched) notes are not supported",
    "transpose": "transposing instruments are not supported",
}


def _local(tag):
    return etree.QName(tag).localname if isinstance(tag, str) else tag


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, name):
    c = _child(el, name)
    return c.text.strip() if c is not None and c.text else None


def _pos_int(el, name, where):
    raw = _text(el, name)
    if raw is None:
        raise ConversionError(f"missing <{name}>", **where)
    try:
        value = int(raw)
    except ValueError:
        raise ConversionError(f"invalid <{name}> value {raw!r}", **where)
    if value <= 0:
        raise ConversionError(f"<{name}> must be positive, got {value}", **where)
    return value


def parse_score(data: bytes) -> Score:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ConversionError(
            f"upload too large: {len(data)} bytes (limit {MAX_UPLOAD_BYTES})"
        )
    head = data[:2048].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ConversionError("DOCTYPE/ENTITY declarations are not allowed")

    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, recover=False
    )
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ConversionError(f"malformed XML: {exc}")

    if _local(root.tag) != "score-partwise":
        raise ConversionError(
            f"root element must be score-partwise, got {_local(root.tag)!r}"
        )

    part_list = _child(root, "part-list")
    if part_list is None:
        raise ConversionError("missing <part-list>")

    score = Score()
    programs = {}
    for sp in _children(part_list, "score-part"):
        pid = sp.get("id")
        if not pid:
            raise ConversionError("<score-part> without id")
        name = _text(sp, "part-name") or pid
        program = 1
        mi = _child(sp, "midi-instrument")
        if mi is not None:
            chan = _text(mi, "midi-channel")
            if chan is not None and int(chan) == 10:
                raise ConversionError(
                    "percussion channel (10) is not supported", part=pid
                )
            up = _child(mi, "midi-unpitched")
            if up is not None:
                raise ConversionError("percussion instrument is not supported", part=pid)
            prog = _text(mi, "midi-program")
            if prog is not None:
                program = int(prog)
                if not 1 <= program <= 128:
                    raise ConversionError(
                        f"midi-program out of range: {program}", part=pid
                    )
        if pid in programs:
            raise ConversionError(f"duplicate part id {pid!r}", part=pid)
        programs[pid] = (name, program)

    part_els = _children(root, "part")
    if not part_els:
        raise ConversionError("no <part> elements found")

    note_count = 0
    for pel in part_els:
        pid = pel.get("id")
        if pid not in programs:
            raise ConversionError(f"<part> references unknown id {pid!r}", part=pid)
        name, program = programs[pid]
        part = Part(part_id=pid, name=name, program=program)
        note_count += _parse_part(pel, part, first=not score.parts)
        if note_count > MAX_NOTES:
            raise ConversionError(
                f"too many notes: exceeds limit of {MAX_NOTES}", part=pid
            )
        score.parts.append(part)
    return score


def _parse_part(pel, part: Part, first: bool) -> int:
    cursor = Fraction(0)          # current time in quarter notes
    measure_start = Fraction(0)
    divisions = None
    open_ties = {}                # (voice, pitch) -> NoteEvent being extended
    prev_start = None
    prev_duration = Fraction(0)
    count = 0

    measures = _children(pel, "measure")
    if not measures:
        raise ConversionError("part has no measures", part=part.part_id)

    for mel in measures:
        mnum = mel.get("number") or "?"
        where = {"part": part.part_id, "measure": mnum}
        measure_max = cursor
        measure_start = cursor

        for el in mel:
            tag = _local(el.tag)
            if tag == "attributes":
                if _child(el, "transpose") is not None:
                    raise ConversionError(
                        "transposing instruments are not supported", **where
                    )
                div_el = _child(el, "divisions")
                if div_el is not None and div_el.text:
                    try:
                        divisions = int(div_el.text.strip())
                    except ValueError:
                        raise ConversionError("invalid <divisions>", **where)
                    if divisions <= 0:
                        raise ConversionError("<divisions> must be positive", **where)
            elif tag == "note":
                count += 1
                nwhere = dict(where, note=count)
                if divisions is None:
                    raise ConversionError("note before any <divisions>", **nwhere)
                dur, is_chord, advance = _parse_note(
                    el, part, divisions, cursor, prev_start, open_ties, nwhere
                )
                if is_chord:
                    pass  # chord notes share the previous start, cursor frozen
                else:
                    prev_start = cursor
                    prev_duration = dur
                    cursor += dur
                if cursor > measure_max:
                    measure_max = cursor
            elif tag == "backup":
                dur = Fraction(_pos_int(el, "duration", where), divisions)
                cursor -= dur
                if cursor < 0:
                    raise ConversionError("backup moved cursor below zero", **where)
            elif tag == "forward":
                dur = Fraction(_pos_int(el, "duration", where), divisions)
                cursor += dur
                if cursor > measure_max:
                    measure_max = cursor
            elif tag == "direction":
                if first:
                    _parse_direction(el, part, divisions, cursor, where)
            elif tag == "barline":
                if _child(el, "repeat") is not None:
                    raise ConversionError("repeat signs are not supported", **where)
            elif tag in ("sound", "print", "harmony", "figured-bass"):
                continue
            # unknown measure-level elements are ignored

        # align: next measure starts at the latest end reached in this one
        cursor = measure_max

    if open_ties:
        (voice, pitch), _ = next(iter(open_ties.items()))
        raise ConversionError(
            f"unclosed tie start on {pitch.step}{pitch.octave} in voice {voice}",
            part=part.part_id,
        )
    part.total_duration = measure_start if not part.events else max(
        (e.start + e.duration for e in part.events), default=Fraction(0)
    )
    return count


def _parse_note(el, part, divisions, cursor, prev_start, open_ties, where):
    for bad, msg in _FORBIDDEN.items():
        if _child(el, bad) is not None:
            raise ConversionError(msg, **where)
    notations = _child(el, "notations")
    if notations is not None and _child(notations, "ornaments") is not None:
        raise ConversionError("ornaments are not supported", **where)

    dur_raw = _pos_int(el, "duration", where)
    duration = Fraction(dur_raw, divisions)
    is_chord = _child(el, "chord") is not None
    voice = _text(el, "voice") or "1"

    rest_el = _child(el, "rest")
    pitch_el = _child(el, "pitch")
    if rest_el is None and pitch_el is None:
        raise ConversionError("note has neither <pitch> nor <rest>", **where)

    pitch = None
    if pitch_el is not None:
        step = (_text(pitch_el, "step") or "").upper()
        if step not in _STEPS:
            raise ConversionError(f"invalid pitch step {step!r}", **where)
        try:
            octave = int(_text(pitch_el, "octave"))
        except (TypeError, ValueError):
            raise ConversionError("invalid or missing <octave>", **where)
        alter_raw = _text(pitch_el, "alter")
        try:
            alter = int(alter_raw) if alter_raw is not None else 0
        except ValueError:
            raise ConversionError(f"invalid <alter> {alter_raw!r}", **where)
        pitch = Pitch(step=step, alter=alter, octave=octave)
        if not 0 <= pitch.midi_number() <= 127:
            raise ConversionError(
                f"pitch {step}{alter:+}{octave} out of MIDI range", **where
            )

    ties = {t.get("type") for t in _children(el, "tie")}
    start = prev_start if is_chord else cursor
    if start is None:
        raise ConversionError("chord note without a preceding note", **where)

    if pitch is None:
        if ties:
            raise ConversionError("rests cannot be tied", **where)
        part.events.append(
            NoteEvent(voice=voice, start=start, duration=duration, measure=where["measure"])
        )
        return duration, is_chord, not is_chord

    key = (voice, pitch)
    if "stop" in ties:
        event = open_ties.get(key)
        if event is None:
            raise ConversionError(
                f"tie stop without matching start on "
                f"{pitch.step}{pitch.octave} in voice {voice}",
                **where,
            )
        if event.start + event.duration != start:
            raise ConversionError("tie chain is not continuous", **where)
        event.duration += duration
        if "start" not in ties:
            del open_ties[key]
    else:
        event = NoteEvent(
            voice=voice, start=start, duration=duration,
            pitch=pitch, measure=where["measure"],
        )
        part.events.append(event)
    if "start" in ties:
        if key in open_ties and "stop" not in ties:
            raise ConversionError(
                f"tie start while another tie is open on "
                f"{pitch.step}{pitch.octave} in voice {voice}",
                **where,
            )
        if "stop" not in ties:
            open_ties[key] = event
    return duration, is_chord, not is_chord


def _parse_direction(el, part, divisions, cursor, where):
    offset_raw = _text(el, "offset")
    offset = Fraction(0)
    if offset_raw is not None:
        try:
            offset = Fraction(int(offset_raw), divisions)
        except ValueError:
            raise ConversionError(f"invalid direction <offset> {offset_raw!r}", **where)
    sound = _child(el, "sound")
    if sound is not None and sound.get("tempo") is not None:
        try:
            bpm = float(sound.get("tempo"))
        except ValueError:
            raise ConversionError("invalid sound tempo", **where)
        if bpm <= 0:
            raise ConversionError("tempo must be positive", **where)
        part.tempos.append(TempoEvent(offset=cursor + offset, bpm=bpm))

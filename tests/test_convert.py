import io
from fractions import Fraction

import mido
import pytest

from app.errors import ConversionError
from app.midi_writer import PPQ, build_midi
from app.parser import parse_score

NS = 'xmlns="http://www.musicxml.org/ns/musicxml"'


def score(body, part_list=None, extra_parts=""):
    part_list = part_list or '<score-part id="P1"><part-name>P</part-name></score-part>'
    return (
        f'<?xml version="1.0"?><score-partwise {NS}>'
        f"<part-list>{part_list}</part-list>"
        f'<part id="P1">{body}</part>{extra_parts}</score-partwise>'
    ).encode()


def measure(n, inner, attrs='<divisions>1</divisions>'):
    a = f"<attributes>{attrs}</attributes>" if attrs else ""
    return f'<measure number="{n}">{a}{inner}</measure>'


def note(step="C", octave=4, dur=1, voice=1, ties="", chord=False, rest=False):
    body = "<rest/>" if rest else f"<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
    c = "<chord/>" if chord else ""
    t = "".join(f'<tie type="{x}"/>' for x in ties.split()) if ties else ""
    return f"<note>{body}{c}<duration>{dur}</duration><voice>{voice}</voice>{t}</note>"


def test_basic_single_voice():
    xml = score(measure(1, note(dur=2) + note(step="E", dur=2)))
    s = parse_score(xml)
    ev = [e for e in s.parts[0].events if e.pitch]
    assert [float(e.start) for e in ev] == [0.0, 2.0]
    mid, summary = build_midi(s)
    assert mid.type == 1 and mid.ticks_per_beat == PPQ
    assert summary["notes"] == 2 and summary["voices"] == 1
    assert summary["total_beats"] == 4.0


def test_chord_does_not_advance_cursor():
    xml = score(measure(1, note(dur=2) + note(step="E", dur=2, chord=True) + note(step="G", dur=2)))
    s = parse_score(xml)
    starts = [float(e.start) for e in s.parts[0].events if e.pitch]
    assert starts == [0.0, 0.0, 2.0]


def test_backup_forward_and_voices():
    inner = note(dur=4) + "<backup><duration>4</duration></backup>" + note(step="E", dur=2, voice=2) + "<forward><duration>2</duration></forward>"
    xml = score(measure(1, inner) + measure(2, note(step="G", dur=4), attrs=None))
    s = parse_score(xml)
    v2 = [e for e in s.parts[0].events if e.voice == "2" and e.pitch]
    assert float(v2[0].start) == 0.0
    m2 = [e for e in s.parts[0].events if e.measure == "2"]
    assert float(m2[0].start) == 4.0  # aligned to max end of measure 1


def test_backup_below_zero_rejected():
    xml = score(measure(1, "<backup><duration>1</duration></backup>" + note()))
    with pytest.raises(ConversionError, match="below zero"):
        parse_score(xml)


def test_tie_chain_merged():
    xml = score(measure(1, note(dur=1, ties="start") + note(dur=1, ties="stop start") + note(dur=2, ties="stop")))
    s = parse_score(xml)
    pitched = [e for e in s.parts[0].events if e.pitch]
    assert len(pitched) == 1
    assert pitched[0].duration == Fraction(4)


def test_orphan_tie_stop_rejected():
    xml = score(measure(1, note(dur=1, ties="stop")))
    with pytest.raises(ConversionError, match="tie stop without matching start"):
        parse_score(xml)


def test_unclosed_tie_start_rejected():
    xml = score(measure(1, note(dur=1, ties="start")))
    with pytest.raises(ConversionError, match="unclosed tie"):
        parse_score(xml)


def test_tie_must_match_pitch():
    xml = score(measure(1, note(dur=1, ties="start") + note(step="E", dur=1, ties="stop")))
    with pytest.raises(ConversionError):
        parse_score(xml)


@pytest.mark.parametrize("snippet,msg", [
    ('<notations><ornaments><trill-mark/></ornaments></notations>', "ornaments"),
    ("<grace/>", "grace"),
    ("<unpitched><display-step>C</display-step><display-octave>4</display-octave></unpitched>", "percussion"),
])
def test_forbidden_note_features(snippet, msg):
    if "unpitched" in snippet:
        n = f"<note>{snippet}<duration>1</duration></note>"
    else:
        n = f"<note><pitch><step>C</step><octave>4</octave></pitch>{snippet}<duration>1</duration></note>"
    with pytest.raises(ConversionError, match=msg):
        parse_score(score(measure(1, n)))


def test_transpose_rejected():
    xml = score(measure(1, note(), attrs="<divisions>1</divisions><transpose><chromatic>2</chromatic></transpose>"))
    with pytest.raises(ConversionError, match="transpos"):
        parse_score(xml)


def test_repeat_rejected():
    xml = score(measure(1, note() + '<barline location="right"><repeat direction="backward"/></barline>'))
    with pytest.raises(ConversionError, match="repeat"):
        parse_score(xml)


def test_doctype_rejected():
    xml = b'<?xml version="1.0"?><!DOCTYPE score-partwise SYSTEM "http://x/y.dtd"><score-partwise/>'
    with pytest.raises(ConversionError, match="DOCTYPE"):
        parse_score(xml)


def test_error_location():
    xml = score(measure(1, note()) + measure(2, "<note><duration>1</duration></note>", attrs=None))
    with pytest.raises(ConversionError) as ei:
        parse_score(xml)
    assert "P1" in str(ei.value) and "measure=2" in str(ei.value)


def test_too_many_voices_rejected():
    inner = "".join(
        note(dur=1, voice=v) + ("<backup><duration>1</duration></backup>" if v < 16 else "")
        for v in range(1, 17)
    )
    s = parse_score(score(measure(1, inner)))
    with pytest.raises(ConversionError, match="channels"):
        build_midi(s)


def test_tempo_track_default_and_events():
    inner = ('<direction><sound tempo="90"/></direction>' + note(dur=2)
             + '<direction><offset>1</offset><sound tempo="150"/></direction>' + note(dur=2))
    s = parse_score(score(measure(1, inner)))
    mid, _ = build_midi(s)
    tempos = [m for m in mid.tracks[0] if m.type == "set_tempo"]
    assert [m.tempo for m in tempos] == [mido.bpm2tempo(90), mido.bpm2tempo(150)]
    # second tempo at 2.5 quarters -> 2400 ticks
    ticks, acc = [], 0
    for m in mid.tracks[0]:
        acc += m.time
        if m.type == "set_tempo":
            ticks.append(acc)
    assert ticks == [0, 3 * PPQ]


def test_default_tempo_120():
    s = parse_score(score(measure(1, note())))
    mid, _ = build_midi(s)
    tempos = [m for m in mid.tracks[0] if m.type == "set_tempo"]
    assert tempos[0].tempo == mido.bpm2tempo(120)


def test_note_off_before_on_same_tick():
    xml = score(measure(1, note(dur=1) + note(step="E", dur=1)))
    mid, _ = build_midi(parse_score(xml))
    track = mid.tracks[1]
    msgs = [(m.time, m.type) for m in track if not m.is_meta]
    assert msgs[2] == (PPQ, "note_off")
    assert msgs[3] == (0, "note_on")


def test_program_from_part_list():
    pl = ('<score-part id="P1"><part-name>Fl</part-name>'
          '<midi-instrument id="P1-I1"><midi-program>74</midi-program></midi-instrument></score-part>')
    s = parse_score(score(measure(1, note()), part_list=pl))
    mid, _ = build_midi(s)
    pc = [m for m in mid.tracks[1] if m.type == "program_change"]
    assert pc[0].program == 73


def test_incomplete_first_measure():
    # pickup measure of one quarter, then a full measure
    xml = score(measure(1, note(dur=1)) + measure(2, note(step="E", dur=4), attrs=None))
    s = parse_score(xml)
    m2 = [e for e in s.parts[0].events if e.measure == "2"]
    assert float(m2[0].start) == 1.0

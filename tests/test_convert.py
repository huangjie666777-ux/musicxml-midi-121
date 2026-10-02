import io

import mido
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise{ns}>
  <part-list>
    <score-part id="P1">
      <part-name>Flute</part-name>
      <midi-instrument id="P1-I1"><midi-program>74</midi-program></midi-instrument>
    </score-part>
    {extra_parts}
  </part-list>
"""

PART2_LIST = """<score-part id="P2"><part-name>Piano</part-name></score-part>"""


def doc(body, ns="", extra_parts="", extra_measures_p1="", part2=""):
    return (HEADER.format(ns=ns, extra_parts=extra_parts)
            + '<part id="P1">' + body + extra_measures_p1 + "</part>"
            + part2 + "</score-partwise>").encode()


def simple_measure(notes):
    return ('<measure number="1"><attributes><divisions>2</divisions>'
            + "<key><fifths>0</fifths></key></attributes>"
            + notes + "</measure>")


def note(step, octave, dur, voice=None, chord=False, ties="", rest=False):
    s = "<note>"
    if chord:
        s += "<chord/>"
    if rest:
        s += "<rest/>"
    else:
        s += f"<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
    s += f"<duration>{dur}</duration>"
    if voice:
        s += f"<voice>{voice}</voice>"
    s += ties
    return s + "</note>"


def convert(xml_bytes):
    resp = client.post("/convert", files={"file": ("score.musicxml", xml_bytes)})
    return resp


def convert_ok(xml_bytes):
    resp = convert(xml_bytes)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    midi = client.get(data["download_url"])
    assert midi.status_code == 200
    return data, mido.MidiFile(file=io.BytesIO(midi.content))


def test_basic_two_voices_chord_rest_backup():
    m1 = simple_measure(
        note("C", 4, 4, voice="1") + note("E", 4, 4, voice="1", chord=True)
        + '<backup><duration>4</duration></backup>'
        + note("G", 3, 2, voice="2") + note("A", 3, 2, voice="2"))
    data, mid = convert_ok(doc(m1))
    assert mid.type == 1 and mid.ticks_per_beat == 960
    assert data["summary"]["voice_count"] == 2
    assert data["summary"]["note_count"] == 4
    assert data["summary"]["total_quarters"] == 2.0
    # tempo track defaults to 120 BPM
    tempos = [m for m in mid.tracks[0] if m.type == "set_tempo"]
    assert tempos and mido.tempo2bpm(tempos[0].tempo) == 120
    # programs: flute = 73 (0-based)
    prog = [m for t in mid.tracks[1:] for m in t if m.type == "program_change"]
    assert prog[0].program == 73
    # channels skip 9
    chans = {m.channel for t in mid.tracks[1:] for m in t
             if hasattr(m, "channel")}
    assert 9 not in chans


def test_tie_chain_merges():
    m1 = simple_measure(
        note("C", 4, 2, ties='<tie type="start"/>')
        + note("C", 4, 2, ties='<tie type="stop"/><tie type="start"/>')
        + note("C", 4, 2, ties='<tie type="stop"/>'))
    data, mid = convert_ok(doc(m1))
    assert data["summary"]["note_count"] == 1
    ons = [m for m in mid.tracks[1] if m.type == "note_on"]
    offs = [m for m in mid.tracks[1] if m.type == "note_off"]
    assert len(ons) == 1 and len(offs) == 1
    total = sum(m.time for m in mid.tracks[1] if m.type in ("note_on", "note_off"))
    assert total == 3 * 960  # 3 quarters sustained


def test_orphan_tie_stop_rejected():
    m1 = simple_measure(note("C", 4, 2, ties='<tie type="stop"/>'))
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "orphan" in resp.json()["detail"]
    assert "part=P1" in resp.json()["detail"]


def test_unclosed_tie_rejected():
    m1 = simple_measure(note("C", 4, 2, ties='<tie type="start"/>'))
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "never closed" in resp.json()["detail"]


def test_noncontiguous_tie_rejected():
    m1 = simple_measure(
        note("C", 4, 2, ties='<tie type="start"/>')
        + note("D", 4, 2)
        + note("C", 4, 2, ties='<tie type="stop"/>'))
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "contiguous" in resp.json()["detail"]


@pytest.mark.parametrize("snippet,word", [
    ('<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration>'
     '<notations><ornaments><trill-mark/></ornaments></notations></note>', "ornaments"),
    ('<note><unpitched><display-step>C</display-step><display-octave>4</display-octave>'
     '</unpitched><duration>2</duration></note>', "percussion"),
    ('<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration>'
     '<grace/></note>', "grace"),
])
def test_rejected_note_features(snippet, word):
    m1 = ('<measure number="1"><attributes><divisions>2</divisions></attributes>'
          + snippet + "</measure>")
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert word in resp.json()["detail"]


def test_transpose_rejected():
    m1 = ('<measure number="1"><attributes><divisions>2</divisions>'
          "<transpose><diatonic>0</diatonic><chromatic>2</chromatic></transpose>"
          "</attributes>" + note("C", 4, 2) + "</measure>")
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "transpos" in resp.json()["detail"]


def test_repeat_rejected():
    m1 = (simple_measure(note("C", 4, 2)).replace(
        "</measure>", '<barline location="right"><repeat direction="backward"/>'
        "</barline></measure>"))
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "repeat" in resp.json()["detail"]


def test_backup_below_zero_rejected():
    m1 = ('<measure number="1"><attributes><divisions>2</divisions></attributes>'
          + note("C", 4, 2) + '<backup><duration>4</duration></backup>'
          + note("D", 4, 2) + "</measure>")
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "backup" in resp.json()["detail"]


def test_forward_and_pickup_measure_alignment():
    # P1: pickup of 1 quarter then full measure; P2 must align by column.
    p1_m1 = ('<measure number="1"><attributes><divisions>2</divisions></attributes>'
             + note("C", 5, 2) + "</measure>")
    p1_m2 = "<measure number=\"2\">" + note("D", 5, 4) + "</measure>"
    p2 = ('<part id="P2">'
          '<measure number="1"><attributes><divisions>2</divisions></attributes>'
          + note("G", 3, 2) + "</measure>"
          '<measure number="2">' + note("E", 3, 4) + '<forward><duration>2</duration>'
          "</forward>" + note("F", 3, 2) + "</measure></part>")
    data, mid = convert_ok(doc(p1_m1, extra_parts=PART2_LIST,
                               extra_measures_p1=p1_m2, part2=p2))
    # column 1: pickup of 1 quarter; column 2: max(2, 2+1+1) = 4 quarters
    assert data["summary"]["total_quarters"] == 5.0
    assert data["summary"]["voice_count"] == 2


def test_namespaced_document():
    m1 = simple_measure(note("C", 4, 4))
    data, _ = convert_ok(doc(m1, ns=' xmlns="http://www.musicxml.org/ns"'))
    assert data["summary"]["note_count"] == 1


def test_tempo_from_direction():
    m1 = ('<measure number="1"><attributes><divisions>2</divisions></attributes>'
          '<direction><direction-type><metronome><beat-unit>quarter</beat-unit>'
          "<per-minute>90</per-minute></metronome></direction-type>"
          '<sound tempo="90"/></direction>'
          + note("C", 4, 4) + "</measure>")
    data, mid = convert_ok(doc(m1))
    tempos = [m for m in mid.tracks[0] if m.type == "set_tempo"]
    assert round(mido.tempo2bpm(tempos[0].tempo)) == 90


def test_too_many_voices_rejected():
    notes = "".join(
        note("C", 4, 2, voice=str(v)) for v in range(1, 17))
    m1 = ('<measure number="1"><attributes><divisions>2</divisions></attributes>'
          + notes + "</measure>")
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "channel" in resp.json()["detail"]


def test_invalid_pitch_rejected():
    m1 = ('<measure number="1"><attributes><divisions>2</divisions></attributes>'
          '<note><pitch><step>H</step><octave>4</octave></pitch>'
          "<duration>2</duration></note></measure>")
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "measure=1" in resp.json()["detail"] and "note=1" in resp.json()["detail"]


def test_failed_conversion_leaves_no_result():
    m1 = simple_measure(note("C", 4, 2, ties='<tie type="start"/>'))
    resp = convert(doc(m1))
    assert resp.status_code == 422
    assert "token" not in resp.json()


def test_unknown_download_token():
    assert client.get("/download/nope").status_code == 404

"""Intermediate score model produced by the parser."""

from dataclasses import dataclass, field
from fractions import Fraction


@dataclass(frozen=True)
class Pitch:
    step: str
    alter: int
    octave: int

    def midi_number(self):
        base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[self.step]
        return 12 * (self.octave + 1) + base + self.alter


@dataclass
class NoteEvent:
    """A sounding note (ties already merged) or a rest."""

    voice: str
    start: Fraction          # in quarter notes, absolute within the part
    duration: Fraction       # in quarter notes
    pitch: Pitch = None      # None means rest
    measure: str = ""


@dataclass
class TempoEvent:
    offset: Fraction         # in quarter notes
    bpm: float


@dataclass
class Part:
    part_id: str
    name: str
    program: int = 1         # MusicXML midi-program, 1-based; 1 = piano
    events: list = field(default_factory=list)   # NoteEvent
    tempos: list = field(default_factory=list)   # TempoEvent (first part only)


@dataclass
class Score:
    parts: list = field(default_factory=list)    # Part

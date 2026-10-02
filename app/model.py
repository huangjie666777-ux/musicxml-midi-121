from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional


@dataclass
class Pitch:
    step: str
    alter: int
    octave: int

    def midi_number(self) -> int:
        base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[self.step]
        return 12 * (self.octave + 1) + base + self.alter

    def key(self):
        return (self.step, self.alter, self.octave)


@dataclass
class NoteEvent:
    """A raw note/rest as read from the score, with absolute time in quarters."""

    part_id: str
    measure_index: int
    note_index: int
    voice: str
    start: Fraction
    duration: Fraction
    pitch: Optional[Pitch]  # None for rests
    tie_start: bool = False
    tie_stop: bool = False

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass
class SustainedNote:
    """A sounding note after tie merging, absolute time in quarters."""

    pitch: Pitch
    start: Fraction
    end: Fraction


@dataclass
class VoiceStaff:
    part_id: str
    part_name: str
    voice: str
    program: int  # GM program, 0-based
    notes: list = field(default_factory=list)  # list[SustainedNote]


@dataclass
class TempoChange:
    time: Fraction  # absolute quarters
    bpm: float


@dataclass
class Score:
    staves: list  # list[VoiceStaff]
    tempos: list  # list[TempoChange]
    total_quarters: Fraction


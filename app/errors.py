"""Error types carrying score location information."""


class ConversionError(Exception):
    """Raised when the score cannot be converted.

    Carries optional location info (part / measure / note index) so the
    API can report exactly where the invalid content lives.
    """

    def __init__(self, message, part=None, measure=None, note=None):
        self.message = message
        self.part = part
        self.measure = measure
        self.note = note
        super().__init__(self._format())

    def _format(self):
        loc = []
        if self.part is not None:
            loc.append(f"part={self.part}")
        if self.measure is not None:
            loc.append(f"measure={self.measure}")
        if self.note is not None:
            loc.append(f"note={self.note}")
        if loc:
            return f"{self.message} ({', '.join(loc)})"
        return self.message

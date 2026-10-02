class ConversionError(Exception):
    """Raised when the MusicXML input cannot be converted.

    The message always locates the offending part / measure / note when known.
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


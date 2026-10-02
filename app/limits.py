"""Hard resource limits for uploads and scores."""

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB uncompressed MusicXML
MAX_NOTES = 200_000                  # total <note> elements across all parts
MAX_VOICES = 15                      # non-percussion MIDI channels available

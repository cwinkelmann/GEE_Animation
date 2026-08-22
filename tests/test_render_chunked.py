"""Chunk-boundary maths for scripts/render_chunked.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from render_chunked import year_chunks


def test_chunks_tile_the_range_without_gaps_or_overlap():
    chunks = year_chunks("2018-01-01", "2026-08-01", 1)
    assert chunks[0] == ("2018-01-01", "2019-01-01")
    assert chunks[-1] == ("2026-01-01", "2026-08-01")   # partial tail kept whole
    # every chunk starts exactly where the previous one ended: no month is
    # rendered twice (a duplicate frame) or skipped (a hole in the series)
    for (_s0, e0), (s1, _e1) in zip(chunks, chunks[1:]):
        assert e0 == s1
    assert chunks[0][0] == "2018-01-01" and chunks[-1][1] == "2026-08-01"


def test_multi_year_chunks_and_short_ranges():
    assert year_chunks("1984-01-01", "2026-08-01", 5)[0] == ("1984-01-01", "1989-01-01")
    # a range shorter than one chunk stays a single chunk
    assert year_chunks("2022-03-01", "2022-09-01", 1) == [("2022-03-01", "2022-09-01")]
    # an empty range yields nothing rather than looping forever
    assert year_chunks("2022-01-01", "2022-01-01", 1) == []

"""Read video frames by time, trusting each frame's own timestamp and not the nominal frame rate."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

type Frame = NDArray[np.uint8]

# The container's duration can be rounded, so a time this close past the end still counts as inside.
END_TOLERANCE_SECONDS = 0.05
# A frame with a repeated or missing timestamp gets this step when the file names no usable rate.
FALLBACK_FRAME_STEP_SECONDS = 0.001
# Below this distance a walk from the start costs about as much as a seek does.
SEEK_MINIMUM_SECONDS = 10.0
# A seek stops this far ahead of the target, so rounding in the seek cannot pass over the frame.
SEEK_MARGIN_SECONDS = 1.0
CADENCE_FRAMES = 8
CADENCE_RELATIVE_TOLERANCE = 0.25
CADENCE_MINIMUM_TOLERANCE_SECONDS = 0.002


class VideoOpenError(Exception):
    """Report that a decoder could not open a video; the caller chooses the user-facing error."""


@dataclass(frozen=True, slots=True)
class SampledFrame:
    """Pair a decoded video frame with its actual requested timestamp."""

    timestamp_seconds: float
    frame: Frame = field(repr=False, compare=False)


class ForwardFrameReader:
    """Walk an open capture forward and return the frame on screen at each requested time.

    The frame on screen is the last one whose timestamp is at or before the time, so a still moment
    of a variable-rate recording resolves to the frame that started it. The reader never seeks,
    because OpenCV computes a seek from the nominal rate (see `read_frame_at`). It requires a
    capture positioned on a frame boundary and left to it alone, holds one decoded frame, and
    returns that same array for repeated requests, so callers must not change it.
    """

    def __init__(self, capture: cv2.VideoCapture) -> None:
        """Grab the first frame; the capture stays owned, and released, by the caller."""
        self._capture = capture
        rate = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self._frame_step = 1.0 / rate if rate > 0 else FALLBACK_FRAME_STEP_SECONDS
        # OpenCV derives the count from the container duration when the file stores none, so this
        # is the real duration for a recording and for a constant-rate file.
        self._reported_duration = frame_count / rate if rate > 0 and frame_count > 0 else 0.0
        self._current: Frame | None = None
        self._last_request = 0.0
        self._last_timestamp = float("-inf")
        self._pending_timestamp: float | None = None
        self._grab_next()

    def starts_by(self, timestamp_seconds: float) -> bool:
        """Return whether the next unread frame exists and is at or before the given time."""
        return self._pending_timestamp is not None and self._pending_timestamp <= timestamp_seconds

    def frame_at(self, timestamp_seconds: float) -> Frame | None:
        """Return the frame on screen at a time, or None past the end or in an empty video.

        Times must not go backward; a lower or non-numeric time raises ValueError, because reaching
        it again would mean decoding the file from the start. A time before the first frame gets
        the first frame. A time after the last frame gets the last frame while it is inside the
        reported duration, since that frame stays on screen until the end.
        """
        if not timestamp_seconds >= self._last_request:
            raise ValueError("Frame times must be numbers that do not go backward.")
        self._last_request = timestamp_seconds
        while self._pending_timestamp is not None and (
            self._pending_timestamp <= timestamp_seconds or self._current is None
        ):
            success, image = self._capture.retrieve()
            if success and image is not None and image.size:
                self._current = cast(Frame, image)
            self._grab_next()
        if self._pending_timestamp is None:
            end = max(self._reported_duration, self._last_timestamp)
            if timestamp_seconds > end + END_TOLERANCE_SECONDS:
                return None
        return self._current

    def _grab_next(self) -> None:
        """Decode the next frame, keeping its picture unconverted until a retrieve asks for it."""
        if not self._capture.grab():
            self._pending_timestamp = None
            return
        reported = float(self._capture.get(cv2.CAP_PROP_POS_MSEC)) / 1_000.0
        # A file without timestamps reports zero for every frame; time must still move on.
        if reported <= self._last_timestamp:
            reported = self._last_timestamp + self._frame_step
        self._last_timestamp = reported
        self._pending_timestamp = reported


def read_frame_bursts(
    capture: cv2.VideoCapture, anchors: Sequence[float], neighbor_offset_seconds: float
) -> Iterator[list[SampledFrame]]:
    """Yield, for each anchor in order, its frames at minus offset, the anchor, and plus offset.

    Requires ascending anchors, a non-negative offset and a capture that stays open while the
    iterator is used; descending anchors raise ValueError. A time past the end is left out, so a
    burst can be empty. A frame shared by several times or bursts is the same array and must not be
    changed. Bursts of neighboring anchors can overlap and the reader cannot go back, so all times
    are read in one ascending pass, and a frame stays in memory only until the last burst that uses
    it has been yielded: at most the frames inside one window of twice the offset, plus one.
    """
    if any(later < earlier for earlier, later in pairwise(anchors)):
        raise ValueError("Frame anchors must be in ascending order.")
    plans = [
        sorted(
            {max(0.0, anchor - neighbor_offset_seconds), anchor, anchor + neighbor_offset_seconds}
        )
        for anchor in anchors
    ]
    schedule = sorted({time for plan in plans for time in plan})
    reader = ForwardFrameReader(capture)
    held: dict[float, Frame | None] = {}
    cursor = 0
    for position, plan in enumerate(plans):
        while cursor < len(schedule) and schedule[cursor] <= plan[-1]:
            held[schedule[cursor]] = reader.frame_at(schedule[cursor])
            cursor += 1
        samples: list[SampledFrame] = []
        for time in plan:
            frame = held[time]
            if frame is not None:
                samples.append(SampledFrame(time, frame))
        yield samples
        if position + 1 < len(plans):
            needed_from = plans[position + 1][0]
            held = {time: frame for time, frame in held.items() if time >= needed_from}


def read_frame_at(video_path: Path, timestamp_seconds: float) -> Frame | None:
    """Return the frame on screen at one time, or None past the end; VideoOpenError if unopenable.

    OpenCV turns a time into a frame number with the nominal rate, seeks to the keyframe before it
    and then grabs the difference in real frames. A recording declares 1000 fps and has far fewer
    frames, so that seek overshoots by a minute or fails, after decoding most of the file. A seek
    is used only when the first frames follow the nominal rate, and its landing is checked by the
    frame's timestamp: otherwise the video is walked from the start, which costs a full decode up
    to the time but always gives the right frame.
    """
    capture = _open_capture(video_path)
    try:
        if timestamp_seconds >= SEEK_MINIMUM_SECONDS and _cadence_matches_nominal_rate(capture):
            capture.set(cv2.CAP_PROP_POS_MSEC, (timestamp_seconds - SEEK_MARGIN_SECONDS) * 1_000.0)
            reader = ForwardFrameReader(capture)
            if reader.starts_by(timestamp_seconds):
                return reader.frame_at(timestamp_seconds)
    finally:
        capture.release()
    capture = _open_capture(video_path)
    try:
        return ForwardFrameReader(capture).frame_at(timestamp_seconds)
    finally:
        capture.release()


def _open_capture(video_path: Path) -> cv2.VideoCapture:
    """Open a decoder for one file, or release it and raise VideoOpenError."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise VideoOpenError("The video could not be opened.")
    return capture


def _cadence_matches_nominal_rate(capture: cv2.VideoCapture) -> bool:
    """Return whether the first frames follow the nominal rate; this consumes those frames."""
    rate = float(capture.get(cv2.CAP_PROP_FPS))
    if rate <= 0:
        return False
    expected = 1.0 / rate
    tolerance = max(expected * CADENCE_RELATIVE_TOLERANCE, CADENCE_MINIMUM_TOLERANCE_SECONDS)
    previous: float | None = None
    for _ in range(CADENCE_FRAMES):
        if not capture.grab():
            return False
        timestamp = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1_000.0
        if previous is not None and abs(timestamp - previous - expected) > tolerance:
            return False
        previous = timestamp
    return True

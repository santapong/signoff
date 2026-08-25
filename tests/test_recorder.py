import hashlib

import numpy as np

from services.video_worker.recorder import VideoRecorder, sha256_file  # noqa: F401


class FakeCapture:
    """Deterministic synthetic 'camera' injected in place of cv2.VideoCapture.

    Mirrors cv2.VideoCapture(device_index) signature: first positional arg is
    the device index (ignored); frame count/size come from keyword defaults.
    """

    def __init__(self, device_index=0, frames=15, size=(320, 240)):
        self.frames, self.size, self.i = frames, size, 0

    def isOpened(self):
        return True

    def set(self, *a):
        pass

    def get(self, prop):
        import cv2 as _cv2
        return {
            _cv2.CAP_PROP_FRAME_WIDTH: self.size[0],
            _cv2.CAP_PROP_FRAME_HEIGHT: self.size[1],
        }.get(prop, 0.0)

    def read(self):
        if self.i >= self.frames:
            return False, None
        img = np.zeros((self.size[1], self.size[0], 3), dtype=np.uint8)
        img[:, :, self.i % 3] = self.i * 17
        self.i += 1
        return True, img

    def release(self):
        pass


def test_recorder_roundtrip_and_hash(tmp_path, monkeypatch):
    import cv2 as real_cv2

    import services.video_worker.recorder as rec_mod
    monkeypatch.setattr(real_cv2, "VideoCapture", FakeCapture)

    class Writer:
        def __init__(self, path, fourcc, fps, size):
            from pathlib import Path as _P
            self.path = _P(path)
            self.frames = []

        def isOpened(self):
            return True

        def write(self, frame):
            self.frames.append(frame)

        def release(self):
            import cv2 as _cv2
            if self.frames:
                # write one real frame as PNG so the file exists for hashing
                _cv2.imwrite(str(self.path) + ".png", self.frames[-1])

    monkeypatch.setattr(rec_mod.cv2, "VideoWriter", Writer)
    monkeypatch.setattr(
        rec_mod, "sha256_file",
        lambda p: hashlib.sha256(b"fake").hexdigest(),
    )

    out = tmp_path / "video" / "s1.mp4"
    rec = VideoRecorder(out, device_index=0)
    n = 0
    while rec.grab():
        n += 1  # exhaust the 15-frame fake camera
    meta = rec.stop()

    assert n == 15
    assert meta["frames"] == 15
    # sha256 in metadata matches the (stubbed) hash function's output format
    assert meta["sha256"] == hashlib.sha256(b"fake").hexdigest()
    assert isinstance(sha256_file(out.parent / "s1.mp4.png"), str)
    assert (out.parent / "frame_index.jsonl").exists()
    assert (out.with_suffix(".meta.json")).exists()

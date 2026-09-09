import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave

import soundfile as sf
from PIL import Image

from desktop_app.media import _continuous_audio, build_timeline, export_audio, export_video
from desktop_app.paths import session_dir
from tests_desktop.test_voices import tone


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="media-test-", dir=session_dir())
        self.root = Path(self.temp.name)
        tone(self.root / "speech.wav", frames=24013)
        Image.new("RGB", (640, 480), "red").save(self.root / "landscape.png")
        Image.new("RGB", (200, 600), "blue").save(self.root / "portrait.png")
        self.slides = [dict(number=i, included=i != 4, text="" if i % 5 == 0 else "讲稿",
                            audio="speech.wav", image="landscape.png" if i % 2 else "portrait.png",
                            blank_seconds=3.0) for i in range(1, 21)]

    def tearDown(self):
        self.temp.cleanup()

    def test_twenty_page_continuous_timeline_rounds_up_without_clipping(self):
        timeline = build_timeline(self.slides, self.root)
        self.assertEqual(len(timeline), 19)
        for previous, current in zip(timeline, timeline[1:]):
            self.assertEqual(current["start_frame"], previous["start_frame"] + previous["frames"])
            self.assertEqual(current["start_sample"], previous["start_sample"] + previous["samples"])
        for page in timeline:
            self.assertEqual(page["samples"], page["frames"] * 800)
            if page["audio"]:
                self.assertGreaterEqual(page["samples"], page["speech_samples"] + 12000)
                self.assertLess(page["samples"] - page["speech_samples"] - 12000, 800)
            else:
                self.assertEqual(page["frames"], 90)
        output = self.root / "continuous.wav"
        _continuous_audio(timeline, output)
        info = sf.info(output)
        self.assertEqual(info.frames, sum(page["samples"] for page in timeline))
        with sf.SoundFile(output) as stream:
            for page in timeline:
                stream.seek(page["start_sample"] + page["speech_samples"])
                silence = stream.read(page["samples"] - page["speech_samples"])
                self.assertTrue((silence == 0).all())

    def test_missing_media_empty_selection_invalid_durations_cancel(self):
        with self.assertRaises(ValueError):
            build_timeline([], self.root)
        self.slides[0]["audio"] = ""
        with self.assertRaises(ValueError):
            build_timeline(self.slides, self.root)
        self.slides[0]["text"] = ""
        self.slides[0]["blank_seconds"] = float("nan")
        with self.assertRaises(ValueError):
            build_timeline(self.slides, self.root)
        timeline = build_timeline([dict(number=1, text="", image="landscape.png")], self.root)
        with self.assertRaises(InterruptedError):
            _continuous_audio(timeline, self.root / "cancel.wav", cancel=lambda: True)

    @unittest.skipUnless(os.environ.get("COSYVOICE_TEST_FFMPEG"), "Set COSYVOICE_TEST_FFMPEG for real codec integration")
    def test_real_wav_mp3_and_h264_aac_export(self):
        ffmpeg = os.environ["COSYVOICE_TEST_FFMPEG"]
        original = self.root / "speech.wav"
        for suffix in ("wav", "mp3"):
            output = self.root / ("导出音频." + suffix)
            self.assertEqual(export_audio(original, output, ffmpeg), str(output))
            self.assertGreater(sf.info(output).frames, 0)
        pages = [dict(number=1, text="讲稿", image="landscape.png", audio="speech.wav"),
                 dict(number=2, text="", image="portrait.png", blank_seconds=0.1)]
        output = self.root / "含 空格视频.mp4"
        result = export_video(pages, self.root, output, ffmpeg, tail_silence=.1)
        self.assertTrue(output.exists())
        self.assertIn(result["encoder"], ("h264_nvenc", "h264_mf"))
        ffprobe = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if ffprobe.exists():
            import json
            probe = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-of", "json", str(output)],
                                   capture_output=True, text=True, check=True)
            streams = json.loads(probe.stdout)["streams"]
            video = next(row for row in streams if row["codec_type"] == "video")
            audio = next(row for row in streams if row["codec_type"] == "audio")
            self.assertEqual(video["codec_name"], "h264")
            self.assertEqual((video["width"], video["height"]), (1920, 1080))
            self.assertEqual(video["r_frame_rate"], "30/1")
            self.assertEqual(int(video["nb_frames"]), result["frames"])
            self.assertEqual(audio["codec_name"], "aac")
            self.assertAlmostEqual(float(video["duration"]), result["duration"], places=4)
            self.assertLessEqual(abs(float(audio["duration"]) - result["duration"]), 1 / 30)

    @unittest.skipUnless(os.environ.get("COSYVOICE_TEST_FFMPEG") and os.name == "nt", "Windows codec integration")
    def test_windows_fallback_and_cancel_preserves_existing_output(self):
        import threading
        ffmpeg = os.environ["COSYVOICE_TEST_FFMPEG"]
        page = dict(number=1, text="", image="portrait.png", blank_seconds=.1)
        output = self.root / "fallback.mp4"
        result = export_video([page], self.root, output, ffmpeg, gpu_index=999)
        self.assertEqual(result["encoder"], "h264_mf")
        before = output.read_bytes()
        cancelled = threading.Event()
        page["blank_seconds"] = 100
        def stop_after_progress(event):
            if event.get("completed", 0) > 0:
                cancelled.set()
        with self.assertRaises(InterruptedError):
            export_video([page], self.root, output, ffmpeg,
                         progress=stop_after_progress, cancel=cancelled.is_set)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

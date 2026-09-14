import unittest

import numpy as np

from desktop_app.audio_join import JoinConfig, plan_boundary_join


SR = 24000


def tone(seconds=1.0, amplitude=0.2, frequency=217, sample_rate=SR):
    t = np.arange(round(seconds * sample_rate), dtype=np.float64) / sample_rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def quiet(seconds, sample_rate=SR):
    return np.zeros(round(seconds * sample_rate), dtype=np.float32)


def apply(left, right, result):
    return np.concatenate((left[:len(left) - result.left_trim_samples], right[result.right_trim_samples:]))


class AudioJoinTests(unittest.TestCase):
    def test_two_long_edges_shorten_to_target_without_changing_inputs(self):
        left = np.concatenate((tone(), quiet(0.8)))
        right = np.concatenate((quiet(0.6), tone(frequency=331)))
        original_left, original_right = left.copy(), right.copy()
        result = plan_boundary_join(left, right, SR)
        self.assertTrue(result.changed)
        self.assertEqual(result.before_samples, round(1.4 * SR))
        self.assertEqual(result.after_samples, round(0.3 * SR))
        self.assertGreaterEqual(result.left_edge_samples - result.left_trim_samples, round(0.08 * SR))
        self.assertGreaterEqual(result.right_edge_samples - result.right_trim_samples, round(0.08 * SR))
        np.testing.assert_array_equal(left, original_left)
        np.testing.assert_array_equal(right, original_right)
        output = apply(left, right, result)
        np.testing.assert_array_equal(output[:SR], tone())
        np.testing.assert_array_equal(output[-SR:], tone(frequency=331))
        self.assertEqual(len(output), len(left) + len(right) - round(1.1 * SR))

    def test_segment_interior_pauses_and_outer_edges_are_untouched(self):
        left = np.concatenate((quiet(0.3), tone(), quiet(1.8), tone(), quiet(0.9)))
        right = np.concatenate((quiet(0.8), tone(), quiet(2.0), tone(), quiet(0.4)))
        result = plan_boundary_join(left, right, SR)
        output = apply(left, right, result)
        self.assertTrue(result.changed)
        left_kept = len(left) - result.left_trim_samples
        np.testing.assert_array_equal(output[:left_kept], left[:left_kept])
        np.testing.assert_array_equal(output[left_kept:], right[result.right_trim_samples:])
        self.assertEqual(np.count_nonzero(output), np.count_nonzero(left) + np.count_nonzero(right))

    def test_short_or_missing_edge_never_inserts_new_silence(self):
        for a, b in ((0, 0), (0.2, 0.2), (0.22, 0.23), (0, 0.44)):
            with self.subTest(edges=(a, b)):
                left, right = np.concatenate((tone(), quiet(a))), np.concatenate((quiet(b), tone()))
                result = plan_boundary_join(left, right, SR)
                self.assertFalse(result.changed)
                np.testing.assert_array_equal(apply(left, right, result), np.concatenate((left, right)))

    def test_one_sided_gap_keeps_other_side_bit_exact(self):
        for swap in (False, True):
            with self.subTest(swap=swap):
                left = tone() if swap else np.concatenate((tone(), quiet(1.2)))
                right = np.concatenate((quiet(1.2), tone())) if swap else tone()
                result = plan_boundary_join(left, right, SR)
                self.assertTrue(result.changed)
                self.assertEqual(result.after_samples, round(0.3 * SR))
                self.assertEqual(result.left_trim_samples if swap else result.right_trim_samples, 0)

    def test_quiet_initial_consonant_and_breath_block_further_scanning(self):
        # These sounds are much quieter than the vowel but exceed the conservative
        # RMS or peak ceiling. Neither they nor a pause behind them may be cut.
        for level in (0.002, 0.004):
            with self.subTest(level=level):
                consonant = tone(0.16, amplitude=level, frequency=3107)
                right = np.concatenate((quiet(0.8), consonant, quiet(0.2), tone()))
                left = np.concatenate((tone(), quiet(0.8)))
                result = plan_boundary_join(left, right, SR)
                self.assertTrue(result.changed)
                self.assertEqual(result.right_edge_samples, round(0.8 * SR))
                self.assertLessEqual(result.right_trim_samples, round(0.72 * SR))
                np.testing.assert_array_equal(apply(left, right, result)[-len(right) + round(0.8 * SR):],
                                              right[round(0.8 * SR):])

    def test_edge_breath_prevents_skipping_to_silence_behind_it(self):
        left = np.concatenate((tone(), quiet(1.0), tone(0.15, amplitude=0.004)))
        right = np.concatenate((quiet(0.2), tone()))
        result = plan_boundary_join(left, right, SR)
        self.assertFalse(result.changed)
        self.assertEqual(result.left_edge_samples, 0)

    def test_low_volume_segments_are_not_trimmed(self):
        for quiet_side in (0, 1, 2):
            with self.subTest(quiet_side=quiet_side):
                left = np.concatenate((tone(amplitude=.003 if quiet_side != 1 else .2), quiet(.8)))
                right = np.concatenate((quiet(.8), tone(amplitude=.003 if quiet_side != 0 else .2)))
                result = plan_boundary_join(left, right, SR)
                self.assertFalse(result.changed)
                self.assertEqual(result.reason, "low_level")

    def test_relative_threshold_protects_low_amplitude_consonant(self):
        # Overall speech is above the low-level cutoff. This consonant would be
        # under the absolute silence threshold, but is above the relative one.
        left = np.concatenate((tone(amplitude=.02), quiet(.8)))
        consonant = tone(.2, amplitude=.0007, frequency=3211)
        right = np.concatenate((quiet(.8), consonant, tone(amplitude=.02)))
        result = plan_boundary_join(left, right, SR)
        self.assertTrue(result.changed)
        self.assertEqual(result.right_edge_samples, round(.8 * SR))
        protected = np.concatenate((consonant, tone(amplitude=.02)))
        np.testing.assert_array_equal(apply(left, right, result)[-len(protected):], protected)

    def test_measured_ppt_boundary_lengths_shorten_but_keep_weak_initial_sound(self):
        # Recreate measured 0.19+0.35 and 0.19+0.47 quiet margins without storing
        # user audio in the repository. The first right segment has a quiet sound
        # before its strong speech; a more aggressive -40dB detector would eat it.
        for leading, with_weak_sound in ((.35, True), (.47, False)):
            with self.subTest(leading=leading):
                weak = tone(.31, amplitude=.002, frequency=3167) if with_weak_sound else quiet(0)
                left = np.concatenate((tone(), quiet(.19)))
                right = np.concatenate((quiet(leading), weak, tone()))
                result = plan_boundary_join(left, right, SR)
                self.assertTrue(result.changed)
                self.assertEqual(result.before_samples, round((.19 + leading) * SR))
                self.assertEqual(result.after_samples, round(.30 * SR))
                self.assertEqual(result.right_edge_samples, round(leading * SR))
                self.assertGreaterEqual(result.right_edge_samples - result.right_trim_samples, round(.08 * SR))
                original_sound = right[round(leading * SR):]
                np.testing.assert_array_equal(apply(left, right, result)[-len(original_sound):], original_sound)

    def test_short_impulse_is_not_sufficient_speech_evidence(self):
        impulse = quiet(1.5)
        impulse[1000] = .9
        result = plan_boundary_join(impulse, np.concatenate((quiet(.8), tone())), SR)
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "low_level")

    def test_peak_ceiling_protects_sparse_edge_sound(self):
        edge = quiet(.1)
        edge[::100] = .005
        left = np.concatenate((tone(), quiet(.8), edge))
        result = plan_boundary_join(left, np.concatenate((quiet(.2), tone())), SR)
        self.assertFalse(result.changed)
        self.assertEqual(result.left_edge_samples, 0)

    def test_guard_protects_uncertain_very_faint_edge_and_window_remainder(self):
        # A sub-threshold 40ms sound falls inside the retained 80ms protection.
        faint = tone(.04, amplitude=.0001)
        right = np.concatenate((quiet(.805), faint, tone()))
        left = np.concatenate((tone(), quiet(.8)))
        result = plan_boundary_join(left, right, SR)
        self.assertTrue(result.changed)
        self.assertLessEqual(result.right_trim_samples, round(.765 * SR))
        protected_start = round(.805 * SR)
        protected = right[protected_start:]
        np.testing.assert_array_equal(apply(left, right, result)[-len(protected):], protected)

    def test_small_nonzero_silence_can_be_shortened_without_gain_or_mix(self):
        noise = tone(.8, amplitude=.00003, frequency=53)
        left, right = np.concatenate((tone(), noise)), np.concatenate((noise, tone()))
        result = plan_boundary_join(left, right, SR)
        self.assertTrue(result.changed)
        output = apply(left, right, result)
        self.assertEqual(len(output), len(left) + len(right) - result.left_trim_samples - result.right_trim_samples)
        np.testing.assert_array_equal(output[:len(left) - result.left_trim_samples], left[:len(left) - result.left_trim_samples])

    def test_sample_rates_and_custom_timing_are_sample_exact(self):
        config = JoinConfig(target_seconds=.43, trigger_seconds=.8, guard_seconds=.1)
        for sr in (16000, 22050, 24000, 44100, 48000):
            with self.subTest(sample_rate=sr):
                left = np.concatenate((tone(sample_rate=sr), quiet(.91, sample_rate=sr)))
                right = np.concatenate((quiet(.83, sample_rate=sr), tone(sample_rate=sr)))
                result = plan_boundary_join(left, right, sr, config)
                self.assertTrue(result.changed)
                self.assertEqual(result.after_samples, round(.43 * sr))
                self.assertGreaterEqual(result.left_edge_samples - result.left_trim_samples, int(np.ceil(.1 * sr)))
                self.assertEqual(result.to_dict()["sample_rate"], sr)

    def test_invalid_or_all_silent_audio_is_rejected(self):
        for bad in (np.empty(0), np.zeros(SR), np.full(SR, 1e-8), np.full(SR, np.nan),
                    np.full(SR, np.inf), np.zeros((SR, 2)), np.ones(SR, dtype=np.int16)):
            for side in (0, 1):
                with self.subTest(shape=bad.shape, side=side), self.assertRaises(ValueError):
                    plan_boundary_join(bad if side == 0 else tone(), tone() if side == 0 else bad, SR)
        for sr in (0, -1, 24000.0, True):
            with self.subTest(sample_rate=sr), self.assertRaises(ValueError):
                plan_boundary_join(tone(), tone(), sr)

    def test_invalid_config_is_rejected(self):
        for kwargs in ({"target_seconds": .8}, {"guard_seconds": .3}, {"window_seconds": .09},
                       {"silence_rms_dbfs": -20}, {"min_active_seconds": 0}, {"speech_percentile": 100},
                       {"target_seconds": float("nan")}, {"trigger_seconds": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                JoinConfig(**kwargs)
        for bad in (False, {}, "default"):
            with self.subTest(config=bad), self.assertRaises(TypeError):
                plan_boundary_join(tone(), tone(), SR, bad)


if __name__ == "__main__":
    unittest.main()

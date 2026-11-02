import hashlib
import unittest
from ic705_codec import CodecSpec, Transform, decompress_stream


class DecoderTests(unittest.TestCase):
    fixture = b'\x07ABC\xee\xf3'

    def test_okumura_overlapping_match(self):
        r = decompress_stream(self.fixture, expected_size=9)
        self.assertEqual(r.data, b'ABCABCABC')
        self.assertEqual(r.consumed_bytes, 6)
        self.assertEqual(r.initial_dictionary_reads, 0)
        self.assertEqual((r.literals, r.matches), (3, 1))
        self.assertEqual(r.status, 'expected_size_reached')
        self.assertFalse(r.verified)

    def test_output_position_masked_literals(self):
        plain = (b'Copyright control 0123456789\n' * 100)
        encoded = bytearray()
        for i in range(0, len(plain), 8):
            group = plain[i:i+8]
            encoded.append((1 << len(group))-1)
            encoded.extend((c+i+j-25) & 255 for j,c in enumerate(group))
        spec = CodecSpec(literals=Transform('sub-output'))
        r = decompress_stream(bytes(encoded), spec, expected_size=len(plain))
        self.assertEqual(r.data, plain)
        self.assertEqual(r.consumed_bytes, len(encoded))
        self.assertFalse(r.verified)

    def test_separate_input_mask_on_flags(self):
        raw = bytes([(7-25+0x10064)&255]) + self.fixture[1:]
        r = decompress_stream(raw, CodecSpec(flags=Transform('sub-input')),
                              base=0x10064, expected_size=9)
        self.assertEqual(r.data, b'ABCABCABC')

    def test_relative_plus_one_and_overlap(self):
        r = decompress_stream(b'\x03AB\x01\x03', CodecSpec(reference='relative-plus-one'), expected_size=8)
        self.assertEqual(r.data, b'ABABABAB')

    def test_invalid_relative_reference(self):
        r = decompress_stream(b'\x00\xee\xf3', CodecSpec(reference='relative'))
        self.assertEqual(r.status, 'invalid_reference')
        self.assertEqual(r.data, b'')
        self.assertFalse(r.verified)

    def test_initial_ring_not_treated_as_validation(self):
        r = decompress_stream(b'\x00\x00\x00')
        self.assertEqual(r.data, b'\0'*3)
        self.assertEqual(r.initial_dictionary_reads, 3)
        self.assertFalse(r.verified)
        strict = decompress_stream(b'\x00\x00\x00', CodecSpec(strict_initial_dictionary=True))
        self.assertEqual(strict.status, 'initial_dictionary_reference')
        self.assertEqual(strict.data, b'')

    def test_explicit_dictionary(self):
        ring = b'XYZ'+bytes(4093)
        r = decompress_stream(b'\x00\x00\x00', CodecSpec(strict_initial_dictionary=True),
                              initial_dictionary=ring)
        self.assertEqual(r.data, b'XYZ')

    def test_truncated_match(self):
        r = decompress_stream(b'\x00\xee')
        self.assertEqual(r.status, 'truncated_token')
        self.assertEqual(r.trace[0]['kind'], 'match')
        self.assertEqual(r.consumed_bytes, 1)

    def test_control_without_token(self):
        self.assertEqual(decompress_stream(b'\xff').status, 'truncated_token')
        self.assertEqual(decompress_stream(b'\xff', CodecSpec(flag_bits=16)).status, 'truncated_flags')

    def test_bounded_output_stops_inside_copy(self):
        r = decompress_stream(self.fixture, max_output=5, expected_size=9)
        self.assertEqual(r.data, b'ABCAB')
        self.assertEqual(r.status, 'output_limit')
        self.assertTrue(r.trace[-1]['stopped_mid_match'])
        self.assertFalse(r.verified)

    def test_missing_expected_output(self):
        r = decompress_stream(b'\x01A', expected_size=20)
        self.assertEqual(r.status, 'truncated_output')
        self.assertEqual(r.data, b'A')

    def test_external_digest_required_for_verified(self):
        digest = hashlib.sha256(b'ABCABCABC').hexdigest()
        r = decompress_stream(self.fixture, expected_size=9, expected_sha256=digest)
        self.assertTrue(r.verified)
        self.assertNotIn('data', r.report())
        mismatch = decompress_stream(self.fixture, expected_size=9, expected_sha256='0'*64)
        self.assertFalse(mismatch.verified)
        self.assertEqual(mismatch.status, 'digest_mismatch')

    def test_trace_limit_does_not_affect_data(self):
        r = decompress_stream(self.fixture, expected_size=9, trace_limit=1, base=0x10064)
        self.assertEqual(r.data, b'ABCABCABC')
        self.assertEqual(len(r.trace), 1)
        self.assertTrue(r.trace_truncated)
        self.assertEqual(r.trace[0]['input_offset'], 0x10065)

    def test_three_byte_match(self):
        r = decompress_stream(b'\x07ABC\xee\x0f\x03',
                              CodecSpec(layout='off16-len8-le'), expected_size=9)
        self.assertEqual(r.data, b'ABCABCABC')

    def test_16_bit_flags(self):
        r = decompress_stream(b'\xff\xffAB', CodecSpec(flag_bits=16), expected_size=2)
        self.assertEqual(r.data, b'AB')

    def test_subheader_skip(self):
        r = decompress_stream(b'header!!'+self.fixture, start=8, expected_size=9)
        self.assertEqual(r.data, b'ABCABCABC')
        self.assertEqual(r.consumed_bytes, 6)

    def test_bad_parameters(self):
        with self.assertRaises(ValueError):
            CodecSpec(window_size=123)
        with self.assertRaises(ValueError):
            Transform('guessed-magic')
        with self.assertRaises(ValueError):
            decompress_stream(b'', expected_sha256='wrong')
        with self.assertRaises(ValueError):
            decompress_stream(b'', start=1)
        with self.assertRaises(ValueError):
            decompress_stream(b'', initial_dictionary=b'x')

    def test_ic705_explicit_hypothesis(self):
        import ic705
        r = ic705.decompress_stream(self.fixture, 'main_block',
                                    hypothesis=CodecSpec(), base=0x10064, expected_size=9)
        self.assertEqual(r.data, b'ABCABCABC')
        self.assertFalse(r.verified)
        with self.assertRaises(ValueError):
            ic705.decompress_stream(self.fixture, 'main_block', hypothesis=CodecSpec())

    def test_ic705_default_stays_explicit(self):
        import ic705
        with self.assertRaises(NotImplementedError):
            ic705.decompress_stream(self.fixture, 'main_block')
        self.assertEqual(ic705.decompress_stream(b'raw', 'wlan_suffix'), b'raw')
        with self.assertRaises(ValueError):
            ic705.decompress_stream(self.fixture, 'main_block', base=0)

    def test_cli_reports_and_refuses_overwrite(self):
        import subprocess
        import sys
        import tempfile
        import json
        from pathlib import Path
        script = Path(__file__).resolve().parent.parent / 'ic705_codec.py'
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source, output, report = tmp/'raw.bin', tmp/'candidate.bin', tmp/'trace.json'
            source.write_bytes(self.fixture)
            cmd = [sys.executable, str(script), str(source), '--raw', '--base', '0x10064',
                   '--expected-size', '9', '--expected-sha256', hashlib.sha256(b'ABCABCABC').hexdigest(),
                   '--output', str(output), '--report', str(report)]
            first = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(output.read_bytes(), b'ABCABCABC')
            self.assertTrue(json.loads(report.read_text())['verified'])
            before = report.read_bytes()
            second = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertIn('Refusing to overwrite', second.stderr)
            self.assertEqual(report.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()

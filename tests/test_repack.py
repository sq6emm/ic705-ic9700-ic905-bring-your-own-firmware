import os
import random
import unittest

from icom_rza1_decode import lzss_decompress, autokey_decode
from ic_rza1_repack import lzss_compress, autokey_encode


class RepackPrimitiveTests(unittest.TestCase):
    def test_lzss_roundtrip_small(self):
        for s in [b"", b"X", b"XY", b"XYZ", b"ABCABCABC", b"A" * 64,
                  bytes(range(20)) * 5, b"hello hello hello world world"]:
            enc = lzss_compress(s)
            dec, _ = lzss_decompress(enc, len(s)) if s else (b"", 0)
            self.assertEqual(dec, s)

    def test_lzss_roundtrip_random_and_structured(self):
        rnd = random.Random(1)
        blobs = [bytes(rnd.getrandbits(8) for _ in range(5000)),
                 (b"ABCD" * 4000),
                 bytes(rnd.randrange(4) for _ in range(8000))]
        for s in blobs:
            self.assertEqual(lzss_decompress(lzss_compress(s), len(s))[0], s)

    def test_autokey_is_exact_inverse(self):
        rnd = random.Random(2)
        data = bytes(rnd.getrandbits(8) for _ in range(9000))
        for base, key, iv in [(0x64, 0x19, 0x9b), (0x38, 0x2b, 0x00), (0x88, 0x7f, 0xff)]:
            enc = autokey_encode(data, base, key, iv)
            dec, _ = autokey_decode(enc, base, key, iv)
            self.assertEqual(dec, data)
            self.assertEqual(autokey_encode(dec, base, key, iv), enc)


@unittest.skipUnless(os.path.exists("705E140.dat"), "needs a user-supplied IC-705 .dat")
class RepackContainerTest(unittest.TestCase):
    def test_identity_repack_valid(self):
        from ic_rza1_repack import _selftest
        _selftest("705E140.dat")


if __name__ == "__main__":
    unittest.main()

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


class PatchOpsTests(unittest.TestCase):
    def test_bootstrap_section_ops(self):
        from icom_patch import apply_ops
        boot = bytes(range(256)) * 256                       # 64 KiB stand-in
        sec = {"ops": [{"at": "0x10", "hex": "deadbeef"},
                       {"at": "0x100", "copy_from": "0x20", "len": 8}]}
        out = apply_ops(boot, sec)
        self.assertEqual(len(out), len(boot))
        self.assertEqual(out[0x10:0x14], bytes.fromhex("deadbeef"))
        self.assertEqual(out[0x100:0x108], boot[0x20:0x28])
        with self.assertRaises(ValueError):
            apply_ops(boot, {"ops": [{"at": "0xfffe", "hex": "000000"}]})

    def test_main_ops_relative_to_load_address(self):
        from icom_patch import apply_ops
        stock = bytes(range(64))
        patch = {"load_address": 0x1000, "image_size": 72,
                 "ops": [{"at": "0x1040", "copy_from": "0x1000", "len": 8}]}
        out = apply_ops(stock, patch)
        self.assertEqual(out, stock + stock[:8])


@unittest.skipUnless(os.path.exists("9700E150.dat"), "needs a user-supplied IC-9700 .dat")
class RepackBootstrapTest(unittest.TestCase):
    def test_new_bootstrap_identity_and_tag_check(self):
        from icom_patch import extract
        from ic_rza1_repack import repack
        raw = open("9700E150.dat", "rb").read()
        image, boot = extract(raw)
        self.assertEqual(repack(raw, image, new_bootstrap=boot), repack(raw, image))
        bad = bytearray(boot); bad[0x4F00] ^= 1
        with self.assertRaises(ValueError):
            repack(raw, image, new_bootstrap=bytes(bad))
        with self.assertRaises(ValueError):
            repack(raw, image, new_bootstrap=boot[:-1])


if __name__ == "__main__":
    unittest.main()

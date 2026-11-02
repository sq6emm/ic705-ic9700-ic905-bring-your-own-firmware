import struct
import unittest

from ic705_decode import feedback_decode, feedback_reverse, arm_elf, lzss_decode


class RecoveredDecoderTests(unittest.TestCase):
    def test_actual_main_header(self):
        # Real E140 bytes and two length words independently recovered from ARM loader semantics.
        stored=bytes.fromhex('e269f56d9361a888')
        clear,previous=feedback_decode(stored,0x10064,255)
        self.assertEqual(struct.unpack('<II',clear),(2071976,3558876))
        self.assertEqual(previous,0)
        reverse,iv=feedback_reverse(stored,0x10064,last_clear=0)
        self.assertEqual(reverse,clear)
        self.assertEqual(iv,255)

    def test_lzss_overlap(self):
        image,consumed=lzss_decode(b'\x07ABC\xee\xf3',9)
        self.assertEqual(image,b'ABCABCABC')
        self.assertEqual(consumed,6)

    def test_truncation_rejected(self):
        with self.assertRaises(ValueError):lzss_decode(b'\x01',1)
        with self.assertRaises(ValueError):lzss_decode(b'\x00\xee',3)

    def test_elf_thumb_entry_and_unaligned_origin(self):
        payload=bytes(range(64));base=0x20000008;entry=0x20000011
        elf=arm_elf(payload,base,entry)
        self.assertEqual(elf[:7],b'\x7fELF\x01\x01\x01')
        self.assertEqual(struct.unpack_from('<H',elf,18)[0],40)
        self.assertEqual(struct.unpack_from('<I',elf,24)[0],entry)
        kind,offset,va,pa,size,memsize,flags,align=struct.unpack_from('<8I',elf,52)
        self.assertEqual((kind,va,pa,size,memsize),(1,base,base,64,64))
        self.assertEqual(offset%align,base%align)
        self.assertEqual(elf[offset:offset+size],payload)


if __name__=='__main__':unittest.main()

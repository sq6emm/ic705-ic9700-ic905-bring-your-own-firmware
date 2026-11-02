#!/usr/bin/env python3
"""Decode IC-705 DAT autokey and real LZSS images, with verified container integrity.

Recovered byte transform is independently present in the decoded Thumb updater:
    clear = stored XOR ((previous_clear + absolute_position - 0x19) & 255)
Bootstrap erased-flash padding (FF) determines the initial feedback byte backwards.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from extract_streams import parse


def feedback_decode(data, base, previous, key=0x19):
    out=bytearray()
    for i,c in enumerate(data):
        previous=c^((previous+base+i-key)&255)
        out.append(previous)
    return bytes(out),previous


def feedback_encode(data, base, previous, key=0x19):
    out=bytearray()
    for i,p in enumerate(data):
        out.append(p^((previous+base+i-key)&255));previous=p
    return bytes(out),previous


def feedback_reverse(data, base, last_clear=255, key=0x19):
    out=bytearray(len(data));p=last_clear
    for i in range(len(data)-1,-1,-1):
        out[i]=p;p=((data[i]^p)-base-i+key)&255
    return bytes(out),p


def lzss_decode(data, expected_size):
    if not 0<expected_size<=64*1024*1024:raise ValueError('Unreasonable output length')
    ring=bytearray(4096);cursor=0xfee;pos=0;flags=0;out=bytearray()
    def get():
        nonlocal pos
        if pos>=len(data):raise ValueError('Truncated LZSS input')
        v=data[pos];pos+=1;return v
    def put(v):
        nonlocal cursor
        out.append(v);ring[cursor]=v;cursor=(cursor+1)&4095
    while len(out)<expected_size:
        flags>>=1
        if not flags&256:flags=get()|0xff00
        if flags&1:put(get())
        else:
            lo,hi=get(),get();index=lo|((hi&240)<<4);length=(hi&15)+3
            for j in range(min(length,expected_size-len(out))):put(ring[(index+j)&4095])
    return bytes(out),pos


def arm_elf(image, base, entry):
    """Minimal standard ELF32 ARM/EABI5 executable, for analysis only."""
    offset=0x1000+(base&0xfff)
    strings=b'\0.text\0.shstrtab\0';string_offset=offset+len(image)
    shoff=(string_offset+len(strings)+3)&~3
    ident=b'\x7fELF\x01\x01\x01'+bytes(9)
    header=struct.pack('<16sHHIIIIIHHHHHH',ident,2,40,1,entry,52,shoff,0x05000000,52,32,1,40,3,2)
    program=struct.pack('<8I',1,offset,base,base,len(image),len(image),7,4096)
    data=bytearray(header+program);data.extend(bytes(offset-len(data)));data.extend(image);data.extend(strings)
    data.extend(bytes(shoff-len(data)));data.extend(bytes(40))
    data.extend(struct.pack('<10I',1,1,7,base,offset,len(image),0,0,4,0))
    data.extend(struct.pack('<10I',7,3,0,0,string_offset,len(strings),0,0,1,0))
    return bytes(data)


def save(path,data):
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('Refusing to overwrite changed artifact: '+str(path))
    else:path.write_bytes(data)
    return dict(file=path.name,size=len(data),sha256=hashlib.sha256(data).hexdigest())


def decode_file(source,directory,independent=False):
    raw=source.read_bytes();container=parse(raw)
    if struct.unpack_from('<I',raw,0x38)[0]!=0x9c0000:raise ValueError('IC-705 layout required')
    directory.mkdir(parents=True,exist_ok=True)
    bootstrap,iv=feedback_reverse(raw[0x64:0x10064],0x64)
    if bootstrap[:8]!=bytes.fromhex('18f09fe518f09fe5'):raise ValueError('Bootstrap vectors not recovered')
    body,end_state=feedback_decode(raw[0x64:0x9c0064],0x64,iv)
    if body[:65536]!=bootstrap:raise ValueError('Forward/reverse bootstrap mismatch')
    rebuilt,_=feedback_encode(body,0x64,iv)
    if rebuilt!=raw[0x64:0x9c0064]:raise ValueError('Autokey round-trip failed')
    records=[];images=[]
    records.append(save(directory/'bootstrap.bin',bootstrap))
    records.append(save(directory/'main_body.clear.bin',body))
    # Loader copy and main address are confirmed by instructions in the actual IC-705 bootstrap.
    images.append(('bootstrap',bootstrap,0x18000000,struct.unpack_from('<I',bootstrap,0x20)[0],'ARMv7-A'))
    images.append(('loader',bootstrap[0x4000:0x49bc],0x20004000,0x20004840,'ARMv7-A'))
    compressed_size,output_size=struct.unpack_from('<II',body,0x10000)
    if not 0<compressed_size<=0x500000-8:raise ValueError('Invalid main stored length')
    main_stream=body[0x10008:0x10008+compressed_size]
    streams=[('main',main_stream,output_size,0x20005000,'ARMv7-A')]
    parts={p['name']:p for p in container['parts']}
    domains=[]
    if parts['aux1']['size'] or parts['aux2']['size']:
        lengths=struct.unpack_from('<4I',body,0x262000)
        expected=(parts['aux1']['size'],parts['aux1']['target_size_field'],parts['aux2']['size'],parts['aux2']['target_size_field'])
        if lengths!=expected:raise ValueError('Embedded auxiliary length header mismatch')
        # Each MD5 domain restarts its counter at 0x4b. Feedback remains continuous
        # inside the shared aux1+aux2 domain, not across a digest boundary.
        first=parts['aux1']['offset'];total=parts['aux1']['size']+parts['aux2']['size']
        cipher=raw[first:first+total];domain_iv=((cipher[0]^255)-0x4b)&255
        aux_clear,_=feedback_decode(cipher,0x64,domain_iv)
        records.append(save(directory/'aux_group.clear.bin',aux_clear))
        domains.append(dict(name='aux1+aux2',initial_feedback=domain_iv,counter_start=0x4b,first_plaintext=255))
        pos=0
        for name,at,base in [('aux1',0x262010,0x20000008),('aux2',0x263000,0x08000000)]:
            n=parts[name]['size'];embedded=body[at:at+n];tail_copy=aux_clear[pos:pos+n];pos+=n
            if embedded!=tail_copy:raise ValueError('Embedded/tail auxiliary plaintext mismatch: '+name)
            streams.append((name,tail_copy,parts[name]['target_size_field'],base,'Thumb'))
    for name in ('dsp','wlan'):
        part=parts[name];cipher=raw[part['offset']:part['offset']+part['size']]
        domain_iv=((cipher[0]^255)-0x4b)&255
        stream,_=feedback_decode(cipher,0x64,domain_iv)
        domains.append(dict(name=name,initial_feedback=domain_iv,counter_start=0x4b,first_plaintext=255))
        streams.append((name,stream,part['target_size_field'],None,None))
    decoded=[]
    for name,stream,size,base,architecture in streams:
        image,consumed=lzss_decode(stream,size)
        tail=stream[consumed:]
        if tail and (name!='main' or len(tail)>3 or any(v!=255 for v in tail)):raise ValueError('Unexpected unconsumed bytes in '+name)
        validation=dict(input_size=len(stream),output_size=size,consumed=consumed,unused_tail_hex=tail.hex())
        if independent:
            from ic705_codec import decompress_stream,CodecSpec
            check=decompress_stream(stream,CodecSpec(),expected_size=size,max_output=size,trace_limit=0)
            if check.error or check.data!=image or check.consumed_bytes!=consumed:raise ValueError('Independent decoder mismatch')
            validation['independent_decoder_identical']=True
        records.append(save(directory/(name+'.lzss.bin'),stream))
        records.append(save(directory/(name+('.application.bin' if base is not None else '.image.bin')),image))
        if base is not None:
            entry=struct.unpack_from('<I',image,0x20 if name=='main' else 0 if name=='aux1' else 4)[0]
            images.append((name,image,base,entry,architecture))
        decoded.append(dict(name=name,**validation))
    imports=[]
    for name,image,base,entry,architecture in images:
        records.append(save(directory/(name+'.elf'),arm_elf(image,base,entry)))
        if name=='loader':records.append(save(directory/'loader.bin',image))
        imports.append(dict(name=name,file=name+'.elf',load_address=hex(base),entry=hex(entry),architecture=architecture,little_endian=True))
    manifest=dict(source=source.name,source_sha256=hashlib.sha256(raw).hexdigest(),
                  container_checks=container['checks'],autokey_key=25,initial_feedback=iv,
                  body_round_trip=True,main_compressed_end=hex(0x1006c+compressed_size),
                  decoded=decoded,imports=imports,artifacts=records,tail_domains=domains,
                  notes=['Main trailing FF is retained; original loader may look ahead after reaching the output length.',
                         'Aux tail plaintext is compared byte-for-byte to embedded main-body copies; lengths are checked against both headers.',
                         'Tail first plaintext byte FF initializes feedback; complete LZSS consumption and output lengths validate each inference.'])
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path);ap.add_argument('directory',type=Path)
    ap.add_argument('--verify-independent',action='store_true')
    args=ap.parse_args();m=decode_file(args.source,args.directory,args.verify_independent)
    print(json.dumps({k:m[k] for k in ('source','initial_feedback','main_compressed_end','decoded','imports')},indent=2))


if __name__=='__main__':main()

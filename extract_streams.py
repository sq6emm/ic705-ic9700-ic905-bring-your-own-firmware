#!/usr/bin/env python3
"""Verified IC705 E/J stream boundaries; leaves all payloads encoded."""
import hashlib,json,struct,argparse
from pathlib import Path

def parse(data):
    if len(data)<100 or data[:4]!=b'D\0wU':raise ValueError('Not an IC705 container')
    f=lambda o:struct.unpack_from('<I',data,o)[0]
    expected=100+f(0x38)+f(0x44)+f(0x4c)+f(0x54)+f(0x5c)+66
    if len(data)!=expected:raise ValueError('Generalized size equation failed')
    p=100+f(0x38)
    if hashlib.md5(data[100:p]).digest()!=data[p:p+16]:raise ValueError('Main MD5 failed')
    checks={'main':True};p+=16;bundle_start=p;parts=[]
    for name,n,target in [('aux1',f(0x44),f(0x48)),('aux2',f(0x4c),f(0x50))]:
        parts.append(dict(name=name,offset=p,size=n,target_size_field=target,md5_scope='aux1+aux2'))
        p+=n
    expected_digest=data[p:p+16]
    if p==bundle_start:
        if expected_digest!=bytes(16):raise ValueError('Missing-aux placeholder is not zero')
        checks['auxiliary']='omitted_zero_placeholder'
    else:
        if hashlib.md5(data[bundle_start:p]).digest()!=expected_digest:raise ValueError('Auxiliary MD5 failed')
        checks['auxiliary']=True
    aux_md5_offset=p;p+=16
    for name,n,target in [('dsp',f(0x54),f(0x58)),('wlan',f(0x5c),f(0x60))]:
        if hashlib.md5(data[p:p+n]).digest()!=data[p+n:p+n+16]:raise ValueError(name+' MD5 failed')
        parts.append(dict(name=name,offset=p,size=n,target_size_field=target,md5_offset=p+n,md5_scope=name))
        checks[name]=True;p+=n+16
    if len(data)-p!=2:raise ValueError('Unexpected footer length')
    return dict(size=len(data),checks=checks,auxiliary_md5_offset=aux_md5_offset,footer_offset=p,
                footer_hex=data[p:].hex(),parts=parts)

def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('source',type=Path);a.add_argument('directory',type=Path);args=a.parse_args()
    data=args.source.read_bytes();manifest=parse(data);args.directory.mkdir(parents=True,exist_ok=True)
    for part in manifest['parts']:
        payload=data[part['offset']:part['offset']+part['size']]
        target=args.directory/(args.source.stem+'_'+part['name']+'.raw.bin')
        if target.exists() and target.read_bytes()!=payload:raise ValueError('Refusing to overwrite changed input')
        target.write_bytes(payload);part['sha256']=hashlib.sha256(payload).hexdigest()
    (args.directory/(args.source.stem+'_manifest.json')).write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()

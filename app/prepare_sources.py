"""Rebuild the received compound Markdown package and safely import the RAR.

Run locally; never executes content from the archive or fetches source URLs.
"""
import argparse
import hashlib
import json
import re
import subprocess
import shutil
import locale
from pathlib import Path, PurePosixPath
from html.parser import HTMLParser
from datetime import datetime, timezone
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from .storage import ROOT

def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def split_package(path):
    text=path.read_text(encoding='utf-8-sig')
    review=text.index('# Анализ источников и ограничения базы')
    faq=text.index('# 140 вопросов и ответов RU / EN')
    target=ROOT/'package';target.mkdir(exist_ok=True)
    for name,content in [('MASTER_PROMPT.md',text[:review]),('SOURCE_REVIEW.md',text[review:faq]),('FAQ_RU_EN.md',text[faq:])]:
        (target/name).write_text(content.strip()+'\n',encoding='utf-8')
    records=[]
    for block in re.split(r'(?=^## FAQ-\d+)',text[faq:],flags=re.M)[1:]:
        header=re.match(r'## (FAQ-\d+) · ([^\n]+)',block)
        sections=re.search(r'\*\*RU — (.*?)\*\*\s*(.*?)\s*\*\*EN — (.*?)\*\*\s*(.*?)\s*Источник: (.*?)\. Статус: (.*?)\.',block,re.S)
        if not header or not sections: raise ValueError('Unparsed FAQ block: '+block[:60])
        qr,ar,qe,ae,source,status=sections.groups()
        records.append({'id':header[1],'category':header[2], 'question':{'ru':qr,'en':qe},'answer':{'ru':ar,'en':ae},
          'source_refs':[x.strip() for x in source.split(';')],'status':'draft','original_status':status,
          'source_file':str(path.name),'source_language':'ru','reconstructed_from_markdown':True})
    if len(records)!=140: raise ValueError(f'Expected 140 FAQ, got {len(records)}')
    write_json(target/'faq_ru_en.json',records)
    write_json(target/'package_manifest.json',{'input':str(path),'sha256':digest(path),'received_format':'compound_markdown',
      'reconstructed_files':['MASTER_PROMPT.md','SOURCE_REVIEW.md','FAQ_RU_EN.md','faq_ru_en.json'],
      'note':'Standalone JSON, previous document_text and OCR files were not supplied. Rebuilt locally; no claim to reproduce prior OCR.'})
    return records

def safe_members(names):
    result=[]
    for name in names:
        p=PurePosixPath(name.replace('\\','/'))
        if p.is_absolute() or '..' in p.parts or any(':' in x or x.endswith(('.', ' ')) for x in p.parts):
            raise ValueError('Unsafe archive member')
        if any(re.match(r'(?i)^(con|nul|prn|aux|com\d|lpt\d)(\.|$)',part) for part in p.parts): raise ValueError('Reserved member')
        result.append(p.as_posix())
    if len(result)>3000 or len(set(x.casefold() for x in result))!=len(result): raise ValueError('Excessive or conflicting archive entries')
    return result

def extract_archive(path):
    target=ROOT/'sources'/'archive';target.mkdir(parents=True,exist_ok=True)
    sha=digest(path); marker=target/'extraction.json'
    if marker.exists() and json.loads(marker.read_text())['archive_sha256']==sha:
        return target/'ChatExport_2026-09-16'
    if any(target.iterdir()): raise ValueError('Extraction target must be empty or match archive checksum')
    listing=subprocess.run(['tar','-tf',str(path)],capture_output=True,check=True).stdout.decode(locale.getencoding())
    names=safe_members(listing.splitlines())
    detailed=subprocess.run(['tar','-tvf',str(path)],capture_output=True,check=True).stdout.decode(locale.getencoding())
    total=0
    for line in detailed.splitlines():
        if not line or line[0] not in ('-','d') or ' link to ' in line or ' -> ' in line: raise ValueError('Links or special archive entries forbidden')
        parts=line.split();total+=int(parts[4])
    if total>1024**3: raise ValueError('Archive exceeds unpacked size limit')
    subprocess.run(['tar','-xf',str(path),'-C',str(target)],check=True,capture_output=True)
    for file in target.rglob('*'):
        if file.is_symlink() or not file.resolve().is_relative_to(target.resolve()): raise ValueError('Unsafe extraction result')
    write_json(marker,{'archive_sha256':sha,'entries':len(names),'declared_bytes':total,'extracted_at':datetime.now(timezone.utc).isoformat()})
    return target/'ChatExport_2026-09-16'

class Node:
    def __init__(self, tag='',attrs=None): self.tag=tag;self.attrs=dict(attrs or []);self.children=[]
    def text(self): return ''.join(x.text() if isinstance(x,Node) else x for x in self.children)
    def all(self, predicate):
        for x in self.children:
            if isinstance(x,Node):
                if predicate(x): yield x
                yield from x.all(predicate)

class Document(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root]
    def handle_starttag(self,tag,attrs):
        node=Node(tag,attrs);self.stack[-1].children.append(node)
        if tag=='br':node.children.append('\n')
        if tag not in ('img','br','meta','link','hr','input','source'): self.stack.append(node)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag: self.stack=self.stack[:i];break
    def handle_data(self,data): self.stack[-1].children.append(data)

def parse_posts(path):
    parser=Document();parser.feed(path.read_text(encoding='utf-8'))
    records=[]
    for node in parser.root.all(lambda n:n.attrs.get('id','').startswith('message') and 'message default' in n.attrs.get('class','')):
        texts=list(node.all(lambda n:n.attrs.get('class','')=='text'))
        dates=list(node.all(lambda n:'date details' in n.attrs.get('class','')))
        links=[n.attrs['href'] for n in node.all(lambda n:n.tag=='a' and 'href' in n.attrs)]
        refs=[x for x in links if not re.match(r'^[a-z]+:|^#',x,re.I)]
        stamp=dates[0].attrs.get('title') if dates else None
        records.append({'id':node.attrs['id'],'text':'\n'.join(n.text().strip() for n in texts),'published_at':stamp,
          'file_path':'archive/ChatExport_2026-09-16/messages.html','anchor':node.attrs['id'],'media':refs,
          'public_url':'https://t.me/biznesvostok/'+node.attrs['id'].replace('message',''),'public_url_verified':False,
          'source_language':'ru','status':'historical'})
    return records

def extract_documents(root):
    import pdfplumber
    records=[];manifest=[]
    for path in sorted((root/'files').iterdir()):
        if path.suffix.lower() not in ('.pdf','.docx'):continue
        pages=[]
        if path.suffix.lower()=='.pdf':
            with pdfplumber.open(path) as doc:
                for n,page in enumerate(doc.pages,1):
                    text=page.extract_text() or ''
                    pages.append({'page':n,'text':text,'status':'extracted' if len(text.strip())>60 else 'OCR_required','reviewed':False})
        else:
            with ZipFile(path) as z:
                if z.getinfo('word/document.xml').file_size>20*1024**2:raise ValueError('Oversized DOCX')
                tree=ET.fromstring(z.read('word/document.xml'))
                ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                rows=[]
                for row in tree.findall('.//w:tr',ns):
                    rows.append(' | '.join(' '.join(t.text or '' for t in cell.findall('.//w:t',ns)) for cell in row.findall('w:tc',ns)))
                paragraphs=[' '.join(t.text or '' for t in p.findall('.//w:t',ns)) for p in tree.findall('.//w:body/w:p',ns)]
                pages=[{'page':1,'text':'\n'.join(paragraphs+rows),'status':'extracted','reviewed':False,'rows':rows}]
        file_id='doc-'+hashlib.sha256(path.name.encode()).hexdigest()[:16]
        records.append({'id':file_id,'filename':path.name,'file_path':str(path.relative_to(ROOT/'sources')).replace('\\','/'),
          'sha256':digest(path),'pages':pages,'status':'source_observed','source_language':'en' if path.name[0].isascii() else 'ru'})
        dest=ROOT/'sources'/'original_documents'/path.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(path,dest)
        manifest.append({'id':file_id,'filename':path.name,'sha256':digest(path),'pages':len(pages)})
    return records,manifest

def main():
    p=argparse.ArgumentParser();p.add_argument('--markdown',type=Path,required=True);p.add_argument('--archive',type=Path,required=True);args=p.parse_args()
    split_package(args.markdown);root=extract_archive(args.archive)
    posts=parse_posts(root/'messages.html');write_json(ROOT/'sources'/'posts.json',posts)
    documents,manifest=extract_documents(root)
    for doc in manifest:
        doc['message_ids']=[p['id'] for p in posts if any(doc['filename'] in x for x in p['media'])]
    write_json(ROOT/'sources'/'document_text.json',documents);write_json(ROOT/'sources'/'documents_manifest.json',manifest)
    inventory=[];gaps=[]
    doc_map={d['filename']:d for d in documents}
    for f in sorted(root.rglob('*')):
        if not f.is_file():continue
        rel=f.relative_to(root).as_posix();suffix=f.suffix.lower()
        status='needs_review';reason='Content has not been visually reviewed'
        if rel.startswith(('images/','css/','js/','stickers/')) or '_thumb.' in rel or f.name.startswith('sticker'):
            status='non_business';reason='Export asset, sticker or derivative thumbnail; not used as business evidence'
        elif suffix in ('.mp4','.mov','.webm'):status='transcription_required';reason='Audio/video not transcribed; caption is separate evidence'
        elif suffix in ('.jpg','.png','.webp'):status='OCR_required';reason='Image not exhaustively reviewed; OCR/visual review required'
        elif suffix in ('.pdf','.docx'):status='extracted';reason='Text extraction only; not verification of every cell'
        elif f.name=='messages.html':status='extracted';reason='Parsed inert HTML; scripts never executed'
        record={'id':'file-'+hashlib.sha256(rel.encode()).hexdigest()[:16],'file_path':'archive/ChatExport_2026-09-16/'+rel,
          'size':f.stat().st_size,'sha256':digest(f),'status':status,'reason':reason,
          'message_ids':[p['id'] for p in posts if rel in p['media']]}
        inventory.append(record)
        if status in ('OCR_required','transcription_required','needs_review'):gaps.append(dict(record))
        if f.name in doc_map:
            for page in doc_map[f.name]['pages']:
                if page['status']=='OCR_required':gaps.append({'id':record['id']+'-p'+str(page['page']),'file_path':record['file_path'],'page':page['page'],'status':'OCR_required','reason':'Insufficient text layer. Prior claimed OCR not attached.'})
    write_json(ROOT/'sources'/'archive_manifest.json',inventory);write_json(ROOT/'sources'/'media_coverage.json',gaps)
    report={'posts':len(posts),'posts_with_text':sum(bool(p['text']) for p in posts),'documents':len(documents),
      'pages':sum(len(d['pages']) for d in documents),'files':len(inventory),'coverage_gaps':len(gaps),'prior_ocr_37_pages':'not supplied; not claimed as imported',
      'missing_media_links':[{'message':p['id'],'path':x} for p in posts for x in p['media'] if not (root/x).exists()]}
    write_json(ROOT/'sources'/'import_report.json',report);print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()

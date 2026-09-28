"""Build a clean, contact-redacted upload copy without changing local evidence."""
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = ('app', 'static', 'tests', 'docs', 'package', 'sources', 'scripts', '.github')
EXTENSIONS = {'.py', '.html', '.css', '.js', '.json', '.md', '.txt', '.yml'}

def scrub(text):
    text = re.sub(r'(?<![\w])\+\d[\d ()-]{8,25}\d', '[contact removed]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email removed]', text)
    return text.replace('C:\\\\Users\\\\user\\\\Downloads\\\\', '')

def main():
    tag = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target = ROOT / 'dist' / ('Export-AA-' + tag)
    target.mkdir(parents=True, exist_ok=False)
    paths = [ROOT / x for x in ('.env.example', '.gitignore', '.gitattributes', 'README.md', 'data/knowledge.json', 'data/missions.json')]
    for folder in FOLDERS:
        paths += [p for p in (ROOT/folder).rglob('*') if p.is_file()
                  and p.suffix in EXTENSIONS
                  and not {'archive', 'original_documents', '__pycache__'}.intersection(p.relative_to(ROOT).parts)]
    for path in sorted(set(paths)):
        rel = path.relative_to(ROOT)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        value = path.read_text(encoding='utf-8-sig')
        # Code has only explicit seeded business contacts; tests/code otherwise stay intact.
        if rel.parts[0] in ('sources', 'package', 'docs'):
            value = scrub(value)
        elif rel.as_posix() == 'app/import_data.py':
            value = scrub(value)
        dest.write_text(value, encoding='utf-8', newline='\n')
    with (target/'RELEASE_CHECKS.txt').open('w', encoding='utf-8') as log:
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=target, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit('Release tests failed; inspect ' + str(target/'RELEASE_CHECKS.txt'))
    check_log = target/'RELEASE_CHECKS.txt'
    check_log.write_text(check_log.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    # Tests may create bytecode. Only explicitly selected text files enter ZIP / Git staging.
    included = sorted(p for p in target.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    manifest = {'built_at':tag, 'contacts_redacted':True, 'runtime_data_included':False,
                'files':{p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in included}}
    (target/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    included.append(target/'RELEASE_MANIFEST.json')
    archive = target.with_suffix('.zip')
    with ZipFile(archive, 'x', ZIP_DEFLATED) as z:
        for path in included:z.write(path, 'Export-AA/' + path.relative_to(target).as_posix())
    print(json.dumps({'folder':str(target),'zip':str(archive),'files':len(included),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}, ensure_ascii=False))

if __name__ == '__main__':main()

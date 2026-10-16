"""Verify and recover the supplied Argon revision without touching the main Git tree."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'presidium-argon-68'
DEST = SOURCE / '.integration'
EXPECTED = {
    'Presidium_v51_RECONSTRUCTED_CODE_UNDER_10MB.zip': ('code', '630f92f76c93fd2cb15b2b68e1df5c459e7fa8724d049df9269b582973bf6238'),
    'Presidium_v51_EDITORIAL_ASSETS_01.zip': ('assets1', 'f03aa7e68d6e9ded2a7179c6fb5dfd2662ce167d41e76ffc10116a2ed0f9572c'),
    'Presidium_v51_EDITORIAL_ASSETS_02.zip': ('assets2', '82379b769c0b38f4a979f2a57ba724808a84d4cf9288e26228abbd76ba092d49'),
    'Presidium_v51_GIT_HISTORY_PART_01.zip': ('history1', '6611daaf5d3f925e90e4616eb1605f797bf217513a62d9fedc434fd444724ea0'),
    'Presidium_v51_GIT_HISTORY_PART_02.zip': ('history2', '8deb4a4d40f3ed4d7fa04f63e7f65c6033b5463e40d872020a51c0671265f713'),
}

def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd).decode('utf-8').strip()

def main():
    DEST.mkdir(exist_ok=True)
    report = {'archives': [], 'recovered': {}}
    chosen = {}
    # Inspect all supplied copies, including explicitly superseded archives.
    for archive in SOURCE.rglob('*.zip'):
        if DEST in archive.parents:
            continue
        canonical = archive.name.replace('(1)', '')
        if canonical not in EXPECTED:
            continue
        label, expected = EXPECTED[canonical]
        digest = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
        valid = digest == expected
        if label == 'code':
            valid = valid and archive.stat().st_size == 2812245
        report['archives'].append({'file': str(archive.relative_to(SOURCE)), 'size': archive.stat().st_size, 'sha256': digest, 'accepted': valid})
        if valid:
            chosen.setdefault(label, archive)
    for label, archive in chosen.items():
        target = DEST / label
        if target.exists():
            raise RuntimeError(f'Refusing to overwrite recovery directory: {target}')
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                name = entry.filename.replace('\\', '/')
                rel = PurePosixPath(name)
                final = (target / name).resolve()
                if rel.is_absolute() or '..' in rel.parts or ':' in name or not final.is_relative_to(target.resolve()) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f'Unsafe archive member: {entry.filename}')
            if z.testzip() is not None:
                raise ValueError(f'Corrupt archive: {archive}')
            target.mkdir()
            z.extractall(target)
        report['recovered'][label] = str(target.relative_to(SOURCE))
    if not all(k in chosen for k in ('code', 'history1', 'history2')):
        raise RuntimeError('Required verified code/history archive missing')
    bundle = DEST / 'argon.bundle'
    with bundle.open('wb') as output:
        for label, name in [('history1', 'git_bundle_part_01.bin'), ('history2', 'git_bundle_part_02.bin')]:
            matches = list((DEST / label).rglob(name))
            if len(matches) != 1:
                raise RuntimeError(f'Expected one {name}')
            with matches[0].open('rb') as part:
                shutil.copyfileobj(part, output)
    subprocess.run(['git', 'bundle', 'verify', str(bundle)], cwd=ROOT, check=True)
    repo = DEST / 'repository'
    subprocess.run(['git', 'clone', str(bundle), str(repo)], check=True)
    report['fsck'] = git('fsck', '--full', cwd=repo)
    report['head'] = git('rev-parse', 'HEAD', cwd=repo)
    report['commits'] = int(git('rev-list', '--count', 'HEAD', cwd=repo))
    assert report['head'] == '5ad57e23788b5add024b48591ba65d98d600b557'
    assert report['commits'] == 68
    code = next((DEST / 'code').rglob('package.json')).parent
    tracked = git('ls-files', cwd=repo).splitlines()
    mismatches, omitted = [], []
    canonical = DEST / 'canonical'
    canonical.mkdir()
    for name in tracked:
        blob = subprocess.check_output(['git', 'cat-file', 'blob', 'HEAD:' + name], cwd=repo)
        restored = canonical / name
        restored.parent.mkdir(parents=True, exist_ok=True)
        restored.write_bytes(blob)
        supplied = code / name
        if not supplied.exists():
            omitted.append(name)
        elif supplied.read_bytes() != blob:
            mismatches.append(name)
    report['code_root'] = str(code.relative_to(SOURCE))
    report['source_mismatches'] = mismatches
    report['source_omitted'] = omitted
    (DEST / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'archives'}, ensure_ascii=True, indent=2))
    if mismatches:
        raise RuntimeError('Extracted source differs from verified Git tree')

if __name__ == '__main__':
    main()

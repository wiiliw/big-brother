"""Offline repository checks. No network or third-party dependencies."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def check(root, today):
    errors, warnings = [], []
    try:
        catalog = json.loads((root / 'docs/catalog.json').read_text(encoding='utf-8'))
        assert isinstance(catalog, list) and catalog
    except (OSError, ValueError, AssertionError) as exc:
        return [f'Invalid catalog: {exc}'], []
    seen = set()
    for entry in catalog:
        try:
            path = entry['path']
            assert isinstance(path, str) and path not in seen
            seen.add(path)
            target = (root / path).resolve()
            assert target.is_relative_to(root.resolve()) and target.is_file()
            assert entry['status'] in ('pending', 'partial', 'verified')
            assert isinstance(entry['review_days'], int) and entry['review_days'] > 0
            assert entry['scope'].strip()
            # YYYY-MM is supported when the legacy source gives only a month.
            as_of = entry['as_of']
            assert re.fullmatch(r'\d{4}-\d{2}(-\d{2})?', as_of)
            cutoff = date.fromisoformat(as_of + '-01' if len(as_of) == 7 else as_of)
            assert cutoff <= today
            reviewed = entry['last_verified']
            if reviewed is not None:
                reviewed = date.fromisoformat(reviewed)
                assert cutoff <= reviewed <= today
            if entry['status'] == 'verified':
                assert reviewed is not None
            if entry['status'] != 'verified':
                warnings.append(f'{path}: {entry["status"]}; {entry["scope"]}')
            if (today - (reviewed or cutoff)).days > entry['review_days']:
                warnings.append(f'{path}: review overdue (as_of={as_of})')
        except (KeyError, TypeError, ValueError, AssertionError, AttributeError):
            errors.append(f'Invalid catalog entry: {entry!r}')
    exempt = {'README.md', 'AGENTS.md', 'CONTRIBUTING.md'}
    for file in sorted(root.rglob('*.md')):
        rel = file.relative_to(root)
        if any(part.startswith('.') for part in rel.parts):
            continue
        text = file.read_text(encoding='utf-8')
        if len(rel.parts) == 1 and file.name not in exempt and str(rel) not in seen:
            errors.append(f'{rel}: missing catalog entry')
        if re.search(r'^(<<<<<<< |=======\s*$|>>>>>>> )', text, re.M):
            errors.append(f'{rel}: unresolved merge conflict')
        # Ignore fenced examples; check simple inline Markdown file links.
        prose = re.sub(r'```.*?```', '', text, flags=re.S)
        for link in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', prose):
            url = urlsplit(link.strip('<>'))
            if url.scheme or url.netloc or not url.path:
                continue
            target = (file.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(root.resolve()) or not target.exists():
                errors.append(f'{rel}: broken local link {link}')
        for code in re.findall(r'\b\d+\.(?:SH|SZ|BJ)\b', prose):
            if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', code):
                errors.append(f'{rel}: malformed stock code {code}')
    skill = root / 'SKILL.md'
    if not skill.exists():
        errors.append('Missing SKILL.md')
    else:
        content = skill.read_text(encoding='utf-8')
        if not re.match(r'\A---\nname: dage-skill\ndescription:.*?\n---\n', content, re.S):
            errors.append('Invalid Skill entrypoint (expected name and description frontmatter)')
    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strict', action='store_true')
    parser.add_argument('--today', type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    errors, warnings = check(ROOT, args.today)
    for item in errors:
        print(f'ERROR: {item}')
    for item in warnings:
        print(f'WARN: {item}')
    print(f'{len(errors)} error(s), {len(warnings)} warning(s)')
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == '__main__':
    raise SystemExit(main())
